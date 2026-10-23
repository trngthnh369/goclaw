package discord

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/nextlevelbuilder/goclaw/internal/bus"
)

func TestSendMediaMessageRejectsOversizeContentWithoutTruncating(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		t.Fatal("Discord API should not be called for oversized content")
	}))
	defer server.Close()

	ch := newTestChannel(t, server)
	img := filepath.Join(t.TempDir(), "image.png")
	if err := os.WriteFile(img, []byte("png"), 0o600); err != nil {
		t.Fatal(err)
	}

	err := ch.Send(context.Background(), bus.OutboundMessage{
		Channel: "discord",
		ChatID:  "channel-1",
		Content: strings.Repeat("đ", 1001), // 2002 bytes in UTF-8
		Media:   []bus.MediaAttachment{{URL: img, ContentType: "image/png"}},
	})
	if err == nil {
		t.Fatal("expected oversized content error")
	}
	if !strings.Contains(err.Error(), "content too long") {
		t.Fatalf("unexpected error: %v", err)
	}
}
