package facebook

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/nextlevelbuilder/goclaw/internal/bus"
)

// fakeReels stands in for both Graph hosts: the API (start, finish, status)
// and rupload (the bytes). Each handler can be replaced per test.
type fakeReels struct {
	mu        sync.Mutex
	calls     []string
	uploaded  []byte
	uploadHdr http.Header
	finish    map[string]string

	startBody  string
	uploadCode int
	finishBody string
	finishCode int
	statuses   []string
}

func (f *fakeReels) record(call string) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.calls = append(f.calls, call)
}

func (f *fakeReels) called(prefix string) bool {
	f.mu.Lock()
	defer f.mu.Unlock()
	for _, c := range f.calls {
		if strings.HasPrefix(c, prefix) {
			return true
		}
	}
	return false
}

func newFakeReels(t *testing.T) (*fakeReels, *GraphClient) {
	t.Helper()
	f := &fakeReels{
		startBody:  `{"video_id":"987654321","upload_url":"https://rupload.facebook.com/video-upload/v25.0/987654321"}`,
		uploadCode: http.StatusOK,
		finishBody: `{"success":true}`,
		finishCode: http.StatusOK,
		statuses: []string{
			`{"status":{"video_status":"processing","processing_phase":{"status":"in_progress"},"publishing_phase":{"status":"not_started"}},"id":"987654321"}`,
			`{"status":{"video_status":"ready","processing_phase":{"status":"complete"},"publishing_phase":{"status":"complete","publish_status":"published"}},"id":"987654321"}`,
		},
	}
	graph := newFakeGraph(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.Method == http.MethodPost && r.URL.Path == "/v25.0/111222333/video_reels" && r.URL.Query().Get("upload_phase") == "finish":
			f.record("finish")
			f.mu.Lock()
			f.finish = map[string]string{}
			for k := range r.URL.Query() {
				f.finish[k] = r.URL.Query().Get(k)
			}
			f.mu.Unlock()
			w.WriteHeader(f.finishCode)
			_, _ = fmt.Fprint(w, f.finishBody)
		case r.Method == http.MethodPost && r.URL.Path == "/v25.0/111222333/video_reels":
			f.record("start " + string(body))
			_, _ = fmt.Fprint(w, f.startBody)
		case r.Method == http.MethodGet && r.URL.Path == "/v25.0/987654321" && r.URL.Query().Get("fields") == "status":
			f.record("status")
			f.mu.Lock()
			next := f.statuses[0]
			if len(f.statuses) > 1 {
				f.statuses = f.statuses[1:]
			}
			f.mu.Unlock()
			_, _ = fmt.Fprint(w, next)
		case r.Method == http.MethodGet && r.URL.Path == "/v25.0/987654321":
			_, _ = fmt.Fprint(w, `{"permalink_url":"/reel/987654321/","id":"987654321"}`)
		default:
			t.Errorf("unexpected Graph request %s %s", r.Method, r.URL.String())
			http.NotFound(w, r)
		}
	}))
	upload := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		f.record("upload " + r.URL.Path)
		f.mu.Lock()
		f.uploaded, f.uploadHdr = body, r.Header.Clone()
		f.mu.Unlock()
		w.WriteHeader(f.uploadCode)
		if f.uploadCode == http.StatusOK {
			_, _ = fmt.Fprint(w, `{"success":true}`)
		} else {
			_, _ = fmt.Fprint(w, `{"error":{"code":1,"message":"upload broke"}}`)
		}
	}))
	t.Cleanup(upload.Close)
	savedUpload, savedInterval := ruploadBase, reelStatusInterval
	ruploadBase, reelStatusInterval = upload.URL, time.Millisecond
	t.Cleanup(func() { ruploadBase, reelStatusInterval = savedUpload, savedInterval })
	return f, graph
}

func writeVideo(t *testing.T, content string) (string, string) {
	t.Helper()
	path := filepath.Join(t.TempDir(), "review.mp4")
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256([]byte(content))
	return path, hex.EncodeToString(sum[:])
}

func notPublished(err error) bool {
	var re *reelError
	return errors.As(err, &re) && re.NotPublished()
}

func TestCreateReelVerified_UploadsFinishesAndWaits(t *testing.T) {
	f, g := newFakeReels(t)
	path, sha := writeVideo(t, "mp4-bytes")

	videoID, err := g.CreateReelVerified(context.Background(), "Chú thích #dulich", path, sha)
	if err != nil {
		t.Fatalf("CreateReelVerified: %v", err)
	}
	if videoID != "987654321" {
		t.Fatalf("video id = %q", videoID)
	}
	if string(f.uploaded) != "mp4-bytes" {
		t.Fatalf("uploaded %q, want the file bytes", f.uploaded)
	}
	if got := f.uploadHdr.Get("Authorization"); got != "OAuth "+fixtureToken {
		t.Fatalf("upload Authorization = %q, want the OAuth scheme", got)
	}
	if f.uploadHdr.Get("Offset") != "0" || f.uploadHdr.Get("File_size") != "9" {
		t.Fatalf("upload headers offset=%q file_size=%q", f.uploadHdr.Get("Offset"), f.uploadHdr.Get("File_size"))
	}
	if f.finish["video_state"] != "PUBLISHED" || f.finish["video_id"] != "987654321" ||
		f.finish["description"] != "Chú thích #dulich" {
		t.Fatalf("finish params = %+v", f.finish)
	}
	if !f.called(`start {"upload_phase":"start"}`) {
		t.Fatalf("start call missing: %v", f.calls)
	}
}

func TestCreateReelVerified_DigestMismatchTouchesNothing(t *testing.T) {
	f, g := newFakeReels(t)
	path, _ := writeVideo(t, "mp4-bytes")
	_, err := g.CreateReelVerified(context.Background(), "c", path, strings.Repeat("a", 64))
	if err == nil || !notPublished(err) {
		t.Fatalf("err = %v, want a not-published digest failure", err)
	}
	if len(f.calls) != 0 {
		t.Fatalf("Graph was called %v, want no call", f.calls)
	}
}

func TestCreateReelVerified_FailureBeforeFinishIsNotPublished(t *testing.T) {
	f, g := newFakeReels(t)
	f.uploadCode = http.StatusInternalServerError
	path, sha := writeVideo(t, "mp4-bytes")
	_, err := g.CreateReelVerified(context.Background(), "c", path, sha)
	if err == nil || !notPublished(err) {
		t.Fatalf("err = %v, want not published", err)
	}
	if f.called("finish") {
		t.Fatal("finish must not be called after a failed upload")
	}
}

func TestSendReelWithoutExactlyOneVideoIsNotPublished(t *testing.T) {
	f, g := newFakeReels(t)
	ch := &Channel{graphClient: g}
	err := ch.Send(context.Background(), bus.OutboundMessage{
		Content:  "caption",
		Metadata: map[string]string{"fb_mode": "reels_post"},
		Media: []bus.MediaAttachment{
			{URL: "first.mp4", ContentType: "video/mp4"},
			{URL: "second.mp4", ContentType: "video/mp4"},
		},
	})
	if err == nil || !notPublished(err) {
		t.Fatalf("err = %v, want a not-published rejection", err)
	}
	if len(f.calls) != 0 {
		t.Fatalf("Graph was called %v, want no call", f.calls)
	}
}

func TestCreateReelVerified_FailureFromFinishOnIsAmbiguous(t *testing.T) {
	for name, mutate := range map[string]func(*fakeReels){
		"finish rejected": func(f *fakeReels) {
			f.finishCode, f.finishBody = http.StatusBadRequest, `{"error":{"code":100,"message":"bad"}}`
		},
		"processing failed": func(f *fakeReels) {
			f.statuses = []string{`{"status":{"video_status":"error","processing_phase":{"status":"error","error":{"message":"codec"}}}}`}
		},
	} {
		t.Run(name, func(t *testing.T) {
			f, g := newFakeReels(t)
			mutate(f)
			path, sha := writeVideo(t, "mp4-bytes")
			_, err := g.CreateReelVerified(context.Background(), "c", path, sha)
			if err == nil || notPublished(err) {
				t.Fatalf("err = %v, want an ambiguous failure", err)
			}
		})
	}
}

func TestGetReelPermalink_MakesPathAbsolute(t *testing.T) {
	_, g := newFakeReels(t)
	link, err := g.GetReelPermalink(context.Background(), "987654321")
	if err != nil || link != "https://www.facebook.com/reel/987654321/" {
		t.Fatalf("link = %q, err = %v", link, err)
	}
}

func TestAllowsPublisher(t *testing.T) {
	ch := &Channel{config: facebookInstanceConfig{Publishers: []string{" vf-director "}}}
	if !ch.AllowsPublisher("vf-director") {
		t.Fatal("a listed co-publisher must be allowed")
	}
	if ch.AllowsPublisher("") || ch.AllowsPublisher("codex") {
		t.Fatal("unlisted or empty agents must not be allowed")
	}
}
