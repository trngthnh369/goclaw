package facebook

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"mime/multipart"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/nextlevelbuilder/goclaw/internal/tools"
)

const (
	graphAPIVersion     = "v25.0"
	maxRetries          = 3
	maxPhotoUploadBytes = 25 << 20 // 25 MiB protects the shared gateway from oversized uploads.
	// maxRetryAfterSec caps the Retry-After sleep to prevent goroutine stalls on abnormal values.
	maxRetryAfterSec = 60
)

// graphAPIBase is the Graph API root. Declared as a variable so tests can
// override it with an httptest.NewServer URL.
var (
	graphAPIBase = "https://graph.facebook.com"
)

// fbIDPattern validates Facebook object IDs: numeric or "{num}_{num}" form (post IDs).
var fbIDPattern = regexp.MustCompile(`^\d+(_\d+)?$`)

// GraphClient wraps the Facebook Graph API for a single page instance.
type GraphClient struct {
	httpClient       *http.Client
	pageAccessToken  string
	pageID           string
	photoUploadSlots chan struct{}
	slotsOnce        sync.Once
}

func (g *GraphClient) getUploadSlots() chan struct{} {
	g.slotsOnce.Do(func() {
		if g.photoUploadSlots == nil {
			g.photoUploadSlots = make(chan struct{}, 2)
		}
	})
	return g.photoUploadSlots
}

// NewGraphClient creates a new GraphClient for the given page.
func NewGraphClient(pageAccessToken, pageID string) *GraphClient {
	return &GraphClient{
		httpClient:       &http.Client{Timeout: 15 * time.Second},
		pageAccessToken:  pageAccessToken,
		pageID:           pageID,
		photoUploadSlots: make(chan struct{}, 2),
	}
}

// VerifyToken checks the page access token by calling GET /me.
func (g *GraphClient) VerifyToken(ctx context.Context) error {
	data, err := g.doRequest(ctx, http.MethodGet, "/me?fields=id,name", nil)
	if err != nil {
		return fmt.Errorf("facebook: token verification failed: %w", err)
	}
	var result struct {
		ID   string `json:"id"`
		Name string `json:"name"`
	}
	if err := json.Unmarshal(data, &result); err != nil {
		return fmt.Errorf("facebook: token verification parse error: %w", err)
	}
	slog.Info("facebook: page token verified", "page_id", result.ID, "name", result.Name)
	return nil
}

// SubscribeApp subscribes the app to the page's webhook events.
func (g *GraphClient) SubscribeApp(ctx context.Context) error {
	if err := validateFBID(g.pageID); err != nil {
		return fmt.Errorf("facebook: subscribe app: %w", err)
	}
	path := fmt.Sprintf("/%s/subscribed_apps?subscribed_fields=feed,messages", g.pageID)
	_, err := g.doRequest(ctx, http.MethodPost, path, nil)
	if err != nil {
		return fmt.Errorf("facebook: subscribe app failed: %w", err)
	}
	slog.Info("facebook: app subscribed to page webhooks", "page_id", g.pageID)
	return nil
}

// GetPost fetches a post by ID with message and story fields.
func (g *GraphClient) GetPost(ctx context.Context, postID string) (*GraphPost, error) {
	if err := validateFBID(postID); err != nil {
		return nil, fmt.Errorf("facebook: get post: %w", err)
	}
	path := fmt.Sprintf("/%s?fields=id,message,story,created_time", postID)
	data, err := g.doRequest(ctx, http.MethodGet, path, nil)
	if err != nil {
		return nil, err
	}
	var post GraphPost
	if err := json.Unmarshal(data, &post); err != nil {
		return nil, fmt.Errorf("facebook: parse post: %w", err)
	}
	return &post, nil
}

// GetComment fetches a single comment by ID.
func (g *GraphClient) GetComment(ctx context.Context, commentID string) (*GraphComment, error) {
	if err := validateFBID(commentID); err != nil {
		return nil, fmt.Errorf("facebook: get comment: %w", err)
	}
	path := fmt.Sprintf("/%s?fields=id,message,from,created_time", commentID)
	data, err := g.doRequest(ctx, http.MethodGet, path, nil)
	if err != nil {
		return nil, err
	}
	var c GraphComment
	if err := json.Unmarshal(data, &c); err != nil {
		return nil, fmt.Errorf("facebook: parse comment: %w", err)
	}
	return &c, nil
}

// GetCommentThread fetches up to limit comments under a parent comment.
func (g *GraphClient) GetCommentThread(ctx context.Context, parentCommentID string, limit int) ([]GraphComment, error) {
	if err := validateFBID(parentCommentID); err != nil {
		return nil, fmt.Errorf("facebook: get comment thread: %w", err)
	}
	if limit <= 0 {
		limit = 10
	}
	path := fmt.Sprintf("/%s/comments?fields=id,message,from,created_time&limit=%d", parentCommentID, limit)
	data, err := g.doRequest(ctx, http.MethodGet, path, nil)
	if err != nil {
		return nil, err
	}
	var resp GraphListResponse[GraphComment]
	if err := json.Unmarshal(data, &resp); err != nil {
		return nil, fmt.Errorf("facebook: parse comment thread: %w", err)
	}
	return resp.Data, nil
}

// ReplyToComment posts a reply to a comment. Returns the new comment ID.
func (g *GraphClient) ReplyToComment(ctx context.Context, commentID, message string) (string, error) {
	if err := validateFBID(commentID); err != nil {
		return "", fmt.Errorf("facebook: reply to comment: %w", err)
	}
	path := fmt.Sprintf("/%s/comments", commentID)
	body := map[string]string{"message": message}
	data, err := g.doRequest(ctx, http.MethodPost, path, body)
	if err != nil {
		return "", err
	}
	var result struct {
		ID string `json:"id"`
	}
	if err := json.Unmarshal(data, &result); err != nil {
		return "", fmt.Errorf("facebook: parse reply result: %w", err)
	}
	return result.ID, nil
}

// SendMessage sends a Messenger message to the given recipient. Returns message ID.
func (g *GraphClient) SendMessage(ctx context.Context, recipientID, message string) (string, error) {
	body := map[string]any{
		"recipient": map[string]string{"id": recipientID},
		"message":   map[string]string{"text": message},
	}
	data, err := g.doRequest(ctx, http.MethodPost, "/me/messages", body)
	if err != nil {
		return "", err
	}
	var result struct {
		MessageID string `json:"message_id"`
	}
	if err := json.Unmarshal(data, &result); err != nil {
		return "", fmt.Errorf("facebook: parse send message result: %w", err)
	}
	return result.MessageID, nil
}

// SendTypingOn sends a typing indicator to the recipient (auto-off after 3s).
func (g *GraphClient) SendTypingOn(ctx context.Context, recipientID string) error {
	body := map[string]any{
		"recipient":     map[string]string{"id": recipientID},
		"sender_action": "typing_on",
	}
	_, err := g.doRequest(ctx, http.MethodPost, "/me/messages", body)
	return err
}

// CreateFeedPost publishes a text post to the page feed. Returns the post ID.
func (g *GraphClient) CreateFeedPost(ctx context.Context, message string) (string, error) {
	if err := validateFBID(g.pageID); err != nil {
		return "", fmt.Errorf("facebook: create feed post: %w", err)
	}
	path := fmt.Sprintf("/%s/feed", g.pageID)
	body := map[string]string{"message": message}
	data, err := g.doRequestOnce(ctx, http.MethodPost, path, body)
	if err != nil {
		return "", fmt.Errorf("facebook: create feed post: %w", err)
	}
	var result struct {
		ID string `json:"id"`
	}
	if err := json.Unmarshal(data, &result); err != nil {
		return "", fmt.Errorf("facebook: parse feed post result: %w", err)
	}
	if result.ID == "" {
		return "", fmt.Errorf("facebook: feed post response missing post ID")
	}
	return result.ID, nil
}

// CreatePhotoPost uploads a local image with caption to the page feed.
func (g *GraphClient) CreatePhotoPost(ctx context.Context, caption, filePath string) (string, error) {
	return g.CreatePhotoPostVerified(ctx, caption, filePath, "")
}

// CreatePhotoPostVerified uploads the exact approved image. The file is opened
// once, identity-checked against its lstat result, optionally digest-verified,
// then streamed from that same handle to avoid TOCTOU and large in-memory buffers.
func (g *GraphClient) CreatePhotoPostVerified(
	ctx context.Context,
	caption string,
	filePath string,
	expectedSHA256 string,
) (string, error) {
	if err := validateFBID(g.pageID); err != nil {
		return "", fmt.Errorf("facebook: create photo post: %w", err)
	}

	preInfo, err := os.Lstat(filePath)
	if err != nil {
		return "", fmt.Errorf("facebook: stat image file: %w", err)
	}
	if err := tools.CheckHardlink(filePath); err != nil {
		return "", fmt.Errorf("facebook: image file check failed: %w", err)
	}
	if preInfo.Mode()&os.ModeSymlink != 0 {
		return "", fmt.Errorf("facebook: image file must not be a symlink")
	}
	if !preInfo.Mode().IsRegular() {
		return "", fmt.Errorf("facebook: image file must be a regular file")
	}

	f, err := os.Open(filePath)
	if err != nil {
		return "", fmt.Errorf("facebook: open image file: %w", err)
	}
	defer f.Close()

	openedInfo, err := f.Stat()
	if err != nil {
		return "", fmt.Errorf("facebook: inspect opened image file: %w", err)
	}
	if !openedInfo.Mode().IsRegular() || !os.SameFile(preInfo, openedInfo) {
		return "", fmt.Errorf("facebook: image file identity changed before upload")
	}
	if openedInfo.Size() > maxPhotoUploadBytes {
		return "", fmt.Errorf("facebook: image file too large: %d bytes exceeds %d", openedInfo.Size(), maxPhotoUploadBytes)
	}

	if expectedSHA256 != "" {
		expectedSHA256 = strings.ToLower(strings.TrimSpace(expectedSHA256))
		decoded, decodeErr := hex.DecodeString(expectedSHA256)
		if decodeErr != nil || len(decoded) != sha256.Size {
			return "", fmt.Errorf("facebook: invalid approved image digest")
		}
		h := sha256.New()
		if _, err := io.Copy(h, io.LimitReader(f, maxPhotoUploadBytes+1)); err != nil {
			return "", fmt.Errorf("facebook: hash image file: %w", err)
		}
		if actual := hex.EncodeToString(h.Sum(nil)); actual != expectedSHA256 {
			return "", fmt.Errorf("facebook: approved image digest mismatch")
		}
		if _, err := f.Seek(0, io.SeekStart); err != nil {
			return "", fmt.Errorf("facebook: rewind image file: %w", err)
		}
	}

	slots := g.getUploadSlots()
	select {
	case slots <- struct{}{}:
		defer func() { <-slots }()
	case <-ctx.Done():
		return "", ctx.Err()
	}

	pipeReader, pipeWriter := io.Pipe()
	multipartWriter := multipart.NewWriter(pipeWriter)
	contentType := multipartWriter.FormDataContentType()
	apiURL := fmt.Sprintf("%s/%s/%s/photos", graphAPIBase, graphAPIVersion, g.pageID)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, apiURL, pipeReader)
	if err != nil {
		pipeReader.Close()
		pipeWriter.Close()
		return "", fmt.Errorf("facebook: build photo upload request: %w", err)
	}
	req.Header.Set("Authorization", "Bearer "+g.pageAccessToken)
	req.Header.Set("Content-Type", contentType)

	writeDone := make(chan error, 1)
	go func() {
		writeErr := func() error {
			if err := multipartWriter.WriteField("caption", caption); err != nil {
				return fmt.Errorf("write caption field: %w", err)
			}
			part, err := multipartWriter.CreateFormFile("source", filepath.Base(filePath))
			if err != nil {
				return fmt.Errorf("create form file: %w", err)
			}
			written, err := io.Copy(part, io.LimitReader(f, maxPhotoUploadBytes+1))
			if err != nil {
				return fmt.Errorf("copy image data: %w", err)
			}
			if written > maxPhotoUploadBytes {
				return fmt.Errorf("image file exceeds %d bytes", maxPhotoUploadBytes)
			}
			return multipartWriter.Close()
		}()
		if writeErr != nil {
			_ = pipeWriter.CloseWithError(writeErr)
		} else {
			_ = pipeWriter.Close()
		}
		writeDone <- writeErr
	}()

	uploadClient := &http.Client{Timeout: 60 * time.Second}
	resp, requestErr := uploadClient.Do(req)
	if requestErr != nil {
		_ = pipeReader.CloseWithError(requestErr)
	}
	writeErr := <-writeDone
	_ = pipeReader.Close()
	if requestErr != nil {
		return "", fmt.Errorf("facebook: photo upload: %w", requestErr)
	}
	if writeErr != nil {
		resp.Body.Close()
		return "", fmt.Errorf("facebook: stream photo upload: %w", writeErr)
	}

	respBody, readErr := io.ReadAll(resp.Body)
	resp.Body.Close()
	if readErr != nil {
		return "", fmt.Errorf("facebook: read photo upload response: %w", readErr)
	}

	g.logRateLimit(resp)

	if resp.StatusCode >= 400 {
		var apiErr graphErrorBody
		if json.Unmarshal(respBody, &apiErr) == nil && apiErr.Error.Code != 0 {
			return "", &graphAPIError{code: apiErr.Error.Code, msg: apiErr.Error.Message}
		}
		return "", fmt.Errorf("facebook: photo upload http %d", resp.StatusCode)
	}

	var result struct {
		PostID string `json:"post_id"`
	}
	if err := json.Unmarshal(respBody, &result); err != nil {
		return "", fmt.Errorf("facebook: parse photo upload result: %w", err)
	}
	if result.PostID == "" {
		return "", fmt.Errorf("facebook: photo upload response missing post ID")
	}
	return result.PostID, nil
}

// graphBackoffBase is the base unit for exponential retry backoff in doRequest.
// Production default = 1s, giving 1s, 2s, 4s... per attempt.
// Tests override to 1ms via newFakeGraph so retry tests don't burn 6s of real
// wall-clock time. Production behavior is unchanged.
var graphBackoffBase = 1 * time.Second

// doRequestOnce executes a single Graph API call without retries.
// Use for non-idempotent publishing endpoints where retrying an ambiguous
// timeout/5xx can create duplicate public posts.
func (g *GraphClient) doRequestOnce(ctx context.Context, method, path string, body any) ([]byte, error) {
	return g.doRequestWithAttempts(ctx, method, path, body, 1)
}

// doRequest executes a Graph API call with retries on transient errors.
// The page access token is passed via Authorization header (never in the URL).
func (g *GraphClient) doRequest(ctx context.Context, method, path string, body any) ([]byte, error) {
	return g.doRequestWithAttempts(ctx, method, path, body, maxRetries)
}

func (g *GraphClient) doRequestWithAttempts(ctx context.Context, method, path string, body any, attempts int) ([]byte, error) {
	if attempts < 1 {
		attempts = 1
	}
	apiURL := fmt.Sprintf("%s/%s%s", graphAPIBase, graphAPIVersion, path)

	for attempt := range attempts {
		if attempt > 0 {
			backoff := time.Duration(1<<uint(attempt-1)) * graphBackoffBase
			select {
			case <-ctx.Done():
				return nil, ctx.Err()
			case <-time.After(backoff):
			}
		}

		var reqBody io.Reader
		if body != nil {
			b, err := json.Marshal(body)
			if err != nil {
				return nil, fmt.Errorf("facebook: marshal request: %w", err)
			}
			reqBody = bytes.NewReader(b)
		}

		req, err := http.NewRequestWithContext(ctx, method, apiURL, reqBody)
		if err != nil {
			return nil, fmt.Errorf("facebook: build request: %w", err)
		}
		// Pass token via header to avoid URL logging exposure.
		req.Header.Set("Authorization", "Bearer "+g.pageAccessToken)
		if body != nil {
			req.Header.Set("Content-Type", "application/json")
		}

		resp, err := g.httpClient.Do(req)
		if err != nil {
			if attempt < attempts-1 {
				slog.Warn("facebook: api request error, retrying", "attempt", attempt+1, "err", err)
				continue
			}
			return nil, fmt.Errorf("facebook: api request: %w", err)
		}

		respBody, readErr := io.ReadAll(resp.Body)
		resp.Body.Close()
		if readErr != nil {
			return nil, fmt.Errorf("facebook: read response: %w", readErr)
		}

		// Proactive rate limit monitoring.
		g.logRateLimit(resp)

		// Retry on 5xx.
		if resp.StatusCode >= 500 && attempt < attempts-1 {
			slog.Warn("facebook: server error, retrying", "status", resp.StatusCode, "attempt", attempt+1)
			continue
		}

		// Parse Graph API error envelope.
		if resp.StatusCode >= 400 {
			var apiErr graphErrorBody
			if json.Unmarshal(respBody, &apiErr) == nil && apiErr.Error.Code != 0 {
				// 24h messaging window violation — not retryable.
				if apiErr.Error.Code == 551 || apiErr.Error.Subcode == 2018109 {
					slog.Warn("facebook: 24h messaging window expired", "code", apiErr.Error.Code)
					return nil, &graphAPIError{code: apiErr.Error.Code, msg: apiErr.Error.Message}
				}
				// Rate limited: sleep and retry (capped).
				if resp.StatusCode == 429 && attempt < attempts-1 {
					retryAfter := parseRetryAfter(resp)
					slog.Warn("facebook: rate limited", "retry_after", retryAfter)
					select {
					case <-ctx.Done():
						return nil, ctx.Err()
					case <-time.After(retryAfter):
					}
					continue
				}
				return nil, &graphAPIError{code: apiErr.Error.Code, msg: apiErr.Error.Message}
			}
			return nil, fmt.Errorf("facebook: http %d", resp.StatusCode)
		}

		return respBody, nil
	}

	// All attempts exhausted (only reachable when every iteration took the continue path).
	return nil, fmt.Errorf("facebook: max attempts exceeded")
}

// logRateLimit parses the X-Business-Use-Case-Usage header and warns when approaching limits.
func (g *GraphClient) logRateLimit(resp *http.Response) {
	usage := resp.Header.Get("X-Business-Use-Case-Usage")
	if usage == "" {
		return
	}
	var parsed map[string][]struct {
		CallCount int `json:"call_count"`
	}
	if err := json.Unmarshal([]byte(usage), &parsed); err != nil {
		return
	}
	for _, entries := range parsed {
		for _, e := range entries {
			if e.CallCount >= 95 {
				slog.Warn("facebook: rate limit critical", "call_count_pct", e.CallCount, "page_id", g.pageID)
			} else if e.CallCount >= 80 {
				slog.Warn("facebook: rate limit warning", "call_count_pct", e.CallCount, "page_id", g.pageID)
			}
		}
	}
}

// parseRetryAfter extracts the Retry-After header, capped at maxRetryAfterSec.
func parseRetryAfter(resp *http.Response) time.Duration {
	val := resp.Header.Get("Retry-After")
	if val == "" {
		return 5 * time.Second
	}
	secs, err := strconv.Atoi(val)
	if err != nil || secs <= 0 {
		return 5 * time.Second
	}
	if secs > maxRetryAfterSec {
		secs = maxRetryAfterSec
	}
	return time.Duration(secs) * time.Second
}

// validateFBID returns an error if id is not a valid Facebook object ID format.
// Facebook IDs are numeric strings, or "{num}_{num}" for post IDs.
func validateFBID(id string) error {
	if id == "" {
		return fmt.Errorf("empty facebook ID")
	}
	if !fbIDPattern.MatchString(id) {
		return fmt.Errorf("invalid facebook ID format: %q", id)
	}
	return nil
}

// graphAPIError is a structured error from the Facebook Graph API.
type graphAPIError struct {
	code int
	msg  string
}

func (e *graphAPIError) Error() string {
	return fmt.Sprintf("facebook graph api error %d: %s", e.code, e.msg)
}

// IsAuthError returns true when the error is an expired or invalid token.
func IsAuthError(err error) bool {
	var ge *graphAPIError
	if !errors.As(err, &ge) {
		return false
	}
	return ge.code == 190 || ge.code == 102
}

// IsPermissionError returns true for permission-denied errors.
func IsPermissionError(err error) bool {
	var ge *graphAPIError
	if !errors.As(err, &ge) {
		return false
	}
	return ge.code == 10 || ge.code == 200
}

// IsRateLimitError returns true for rate limit errors.
func IsRateLimitError(err error) bool {
	var ge *graphAPIError
	if !errors.As(err, &ge) {
		return false
	}
	return ge.code == 4 || ge.code == 17 || ge.code == 32 || ge.code == 613
}
