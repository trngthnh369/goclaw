package facebook

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/nextlevelbuilder/goclaw/internal/tools"
)

// Reels go out through the Graph API Reels endpoint in four steps:
//
//  1. POST /{page_id}/video_reels upload_phase=start           -> video_id
//  2. POST rupload.facebook.com/video-upload/{v}/{video_id}    the file bytes
//  3. POST /{page_id}/video_reels upload_phase=finish, video_state=PUBLISHED
//  4. GET  /{video_id}?fields=status until publishing completes
//
// Steps 1 and 2 cannot make anything public: an upload session that is never
// finished simply expires. Their failures are marked NotPublished, which lets
// the message tool release the approval ledger so the same draft can be
// approved again. From step 3 on a failure is ambiguous and stays reserved.
// Every call is made once; a retried publish can create a second public reel.

// ruploadBase is the upload host for step 2. A variable so tests can point it
// at an httptest server; the host is never taken from an API response.
var ruploadBase = "https://rupload.facebook.com"

// reelStatusInterval is how often step 4 polls. A variable so tests do not wait.
var reelStatusInterval = 5 * time.Second

const (
	maxReelUploadBytes = 100 << 20
	reelUploadTimeout  = 3 * time.Minute
	// reelStatusTimeout bounds step 4. Past it the reel may still go live, so
	// the outcome is reported as unknown rather than failed.
	reelStatusTimeout = 4 * time.Minute
)

// reelError records which step of a reel publish failed.
type reelError struct {
	phase string // prepare, start, upload, finish, status
	err   error
}

func (e *reelError) Error() string { return fmt.Sprintf("facebook reel %s: %v", e.phase, e.err) }

func (e *reelError) Unwrap() error { return e.err }

// NotPublished reports whether the failure happened before Facebook was asked
// to publish, so nothing can have gone public.
func (e *reelError) NotPublished() bool {
	switch e.phase {
	case "prepare", "start", "upload":
		return true
	}
	return false
}

func reelFailed(phase string, err error) error { return &reelError{phase: phase, err: err} }

// CreateReelVerified publishes the approved video as a Reel on the page and
// waits until Facebook reports it published. Returns the video id.
//
// The file is opened once, identity-checked against its lstat result and
// digest-verified, then streamed from that same handle, exactly like
// CreatePhotoPostVerified.
func (g *GraphClient) CreateReelVerified(ctx context.Context, caption, filePath, expectedSHA256 string) (string, error) {
	if err := validateFBID(g.pageID); err != nil {
		return "", reelFailed("prepare", err)
	}
	file, size, err := openVerifiedReel(filePath, expectedSHA256)
	if err != nil {
		return "", reelFailed("prepare", err)
	}
	defer file.Close()

	slots := g.getUploadSlots()
	select {
	case slots <- struct{}{}:
		defer func() { <-slots }()
	case <-ctx.Done():
		return "", reelFailed("prepare", ctx.Err())
	}

	videoID, err := g.startReelUpload(ctx)
	if err != nil {
		return "", reelFailed("start", err)
	}
	if err := g.uploadReel(ctx, videoID, file, size); err != nil {
		return "", reelFailed("upload", err)
	}
	if err := g.finishReel(ctx, videoID, caption); err != nil {
		return "", reelFailed("finish", err)
	}
	if err := g.waitReelPublished(ctx, videoID); err != nil {
		return videoID, reelFailed("status", err)
	}
	return videoID, nil
}

func openVerifiedReel(filePath, expectedSHA256 string) (*os.File, int64, error) {
	expected := strings.ToLower(strings.TrimSpace(expectedSHA256))
	if decoded, err := hex.DecodeString(expected); err != nil || len(decoded) != sha256.Size {
		return nil, 0, fmt.Errorf("a valid approved video digest is required")
	}
	preInfo, err := os.Lstat(filePath)
	if err != nil {
		return nil, 0, fmt.Errorf("stat video file: %w", err)
	}
	if err := tools.CheckHardlink(filePath); err != nil {
		return nil, 0, fmt.Errorf("video file check failed: %w", err)
	}
	if preInfo.Mode()&os.ModeSymlink != 0 || !preInfo.Mode().IsRegular() {
		return nil, 0, fmt.Errorf("video file must be a regular file")
	}
	file, err := os.Open(filePath)
	if err != nil {
		return nil, 0, fmt.Errorf("open video file: %w", err)
	}
	openedInfo, err := file.Stat()
	if err != nil || !openedInfo.Mode().IsRegular() || !os.SameFile(preInfo, openedInfo) {
		file.Close()
		return nil, 0, fmt.Errorf("video file identity changed before upload")
	}
	size := openedInfo.Size()
	if size <= 0 || size > maxReelUploadBytes {
		file.Close()
		return nil, 0, fmt.Errorf("video file is %d bytes; allowed 1-%d", size, maxReelUploadBytes)
	}
	h := sha256.New()
	if _, err := io.Copy(h, io.LimitReader(file, maxReelUploadBytes+1)); err != nil {
		file.Close()
		return nil, 0, fmt.Errorf("hash video file: %w", err)
	}
	if hex.EncodeToString(h.Sum(nil)) != expected {
		file.Close()
		return nil, 0, fmt.Errorf("approved video digest mismatch")
	}
	if _, err := file.Seek(0, io.SeekStart); err != nil {
		file.Close()
		return nil, 0, fmt.Errorf("rewind video file: %w", err)
	}
	return file, size, nil
}

func (g *GraphClient) startReelUpload(ctx context.Context) (string, error) {
	data, err := g.doRequestOnce(ctx, http.MethodPost, "/"+g.pageID+"/video_reels",
		map[string]string{"upload_phase": "start"})
	if err != nil {
		return "", err
	}
	var result struct {
		VideoID string `json:"video_id"`
	}
	if err := json.Unmarshal(data, &result); err != nil {
		return "", fmt.Errorf("parse start response: %w", err)
	}
	if err := validateFBID(result.VideoID); err != nil {
		return "", fmt.Errorf("start response carried no usable video_id: %w", err)
	}
	return result.VideoID, nil
}

// uploadReel sends the file to the upload host. That host takes the token as
// "Authorization: OAuth <token>", not "Bearer".
func (g *GraphClient) uploadReel(ctx context.Context, videoID string, file io.Reader, size int64) error {
	uploadURL := fmt.Sprintf("%s/video-upload/%s/%s", ruploadBase, graphAPIVersion, videoID)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, uploadURL, io.LimitReader(file, size))
	if err != nil {
		return fmt.Errorf("build upload request: %w", err)
	}
	req.ContentLength = size
	req.Header.Set("Authorization", "OAuth "+g.pageAccessToken)
	req.Header.Set("offset", "0")
	req.Header.Set("file_size", strconv.FormatInt(size, 10))
	req.Header.Set("Content-Type", "application/octet-stream")

	resp, err := (&http.Client{Timeout: reelUploadTimeout}).Do(req)
	if err != nil {
		// *url.Error carries the URL, which holds no credential here.
		return fmt.Errorf("upload request: %w", err)
	}
	body, readErr := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	resp.Body.Close()
	if readErr != nil {
		return fmt.Errorf("read upload response: %w", readErr)
	}
	g.logRateLimit(resp)
	if resp.StatusCode >= 400 {
		var apiErr graphErrorBody
		if json.Unmarshal(body, &apiErr) == nil && apiErr.Error.Code != 0 {
			return &graphAPIError{code: apiErr.Error.Code, msg: apiErr.Error.Message}
		}
		return fmt.Errorf("upload http %d", resp.StatusCode)
	}
	var result struct {
		Success bool `json:"success"`
	}
	if err := json.Unmarshal(body, &result); err != nil || !result.Success {
		return fmt.Errorf("upload was not acknowledged")
	}
	return nil
}

func (g *GraphClient) finishReel(ctx context.Context, videoID, caption string) error {
	params := url.Values{}
	params.Set("upload_phase", "finish")
	params.Set("video_id", videoID)
	params.Set("video_state", "PUBLISHED")
	params.Set("description", caption)
	data, err := g.doRequestOnce(ctx, http.MethodPost, "/"+g.pageID+"/video_reels?"+params.Encode(), nil)
	if err != nil {
		return err
	}
	var result struct {
		Success bool `json:"success"`
	}
	if err := json.Unmarshal(data, &result); err != nil || !result.Success {
		return fmt.Errorf("finish was not acknowledged")
	}
	return nil
}

type reelPhase struct {
	Status        string `json:"status"`
	PublishStatus string `json:"publish_status"`
	Error         struct {
		Message string `json:"message"`
	} `json:"error"`
}

type reelStatus struct {
	VideoStatus     string    `json:"video_status"`
	UploadingPhase  reelPhase `json:"uploading_phase"`
	ProcessingPhase reelPhase `json:"processing_phase"`
	PublishingPhase reelPhase `json:"publishing_phase"`
}

// errReelStillProcessing means Facebook had not finished publishing when the
// wait ran out. The reel may still go live.
var errReelStillProcessing = errors.New("facebook is still processing the reel")

// waitReelPublished polls the video's status until it is published, failed,
// or reelStatusTimeout passes.
func (g *GraphClient) waitReelPublished(ctx context.Context, videoID string) error {
	deadline := time.Now().Add(reelStatusTimeout)
	for {
		status, err := g.reelStatus(ctx, videoID)
		switch {
		case err != nil:
			slog.Warn("facebook: reel status check failed", "video_id", videoID, "error", err)
		case reelPublished(status):
			return nil
		default:
			if failure := reelFailure(status); failure != "" {
				return fmt.Errorf("facebook rejected the reel: %s", failure)
			}
		}
		if time.Now().After(deadline) {
			return errReelStillProcessing
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(reelStatusInterval):
		}
	}
}

func (g *GraphClient) reelStatus(ctx context.Context, videoID string) (reelStatus, error) {
	var result struct {
		Status reelStatus `json:"status"`
	}
	data, err := g.doRequest(ctx, http.MethodGet, "/"+videoID+"?fields=status", nil)
	if err != nil {
		return result.Status, err
	}
	if err := json.Unmarshal(data, &result); err != nil {
		return result.Status, fmt.Errorf("parse reel status: %w", err)
	}
	return result.Status, nil
}

func reelPublished(s reelStatus) bool {
	done := s.PublishingPhase.Status == "complete" || s.PublishingPhase.Status == "completed"
	return s.PublishingPhase.PublishStatus == "published" || (done && s.VideoStatus == "ready")
}

// reelFailure returns Facebook's reason when any phase reports an error.
func reelFailure(s reelStatus) string {
	for _, phase := range []struct {
		name string
		p    reelPhase
	}{{"upload", s.UploadingPhase}, {"processing", s.ProcessingPhase}, {"publishing", s.PublishingPhase}} {
		if phase.p.Status == "error" || phase.p.PublishStatus == "error" {
			if phase.p.Error.Message != "" {
				return phase.name + ": " + phase.p.Error.Message
			}
			return phase.name + " failed"
		}
	}
	switch s.VideoStatus {
	case "error", "upload_failed", "expired":
		return "video status " + s.VideoStatus
	}
	return ""
}

// GetReelPermalink returns the public URL of a published reel. Graph reports a
// video's permalink as a path on facebook.com; it is made absolute here.
func (g *GraphClient) GetReelPermalink(ctx context.Context, videoID string) (string, error) {
	link, err := g.GetPostPermalink(ctx, videoID)
	if err != nil || link == "" || strings.HasPrefix(link, "http") {
		return link, err
	}
	return "https://www.facebook.com/" + strings.TrimPrefix(link, "/"), nil
}

// ProbeReelsPublishing checks, without publishing anything, that the page
// token may open a Reels upload session: it runs step 1 only and reads the new
// session's status. The session expires unused. Used before switching Reels
// publishing on, so a missing permission is found by an operator, not by the
// first approved video.
func ProbeReelsPublishing(ctx context.Context, pageAccessToken, pageID string) (videoID, videoStatus string, err error) {
	g := NewGraphClient(pageAccessToken, pageID)
	if err := validateFBID(pageID); err != nil {
		return "", "", err
	}
	videoID, err = g.startReelUpload(ctx)
	if err != nil {
		return "", "", fmt.Errorf("upload_phase=start: %w", err)
	}
	status, err := g.reelStatus(ctx, videoID)
	if err != nil {
		return videoID, "", fmt.Errorf("status of new session: %w", err)
	}
	return videoID, status.VideoStatus, nil
}
