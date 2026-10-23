package tools

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/nextlevelbuilder/goclaw/internal/bus"
)

func TestDelegateSyncStagesMediaIntoParentWorkspace(t *testing.T) {
	parentWorkspace := t.TempDir()
	childWorkspace := t.TempDir()
	childImage := filepath.Join(childWorkspace, "image.png")
	if err := os.WriteFile(childImage, []byte("png"), 0o600); err != nil {
		t.Fatal(err)
	}

	ctx := WithToolWorkspace(context.Background(), parentWorkspace)
	tool := &DelegateTool{}
	staged := tool.stageSyncDelegateMedia(ctx, "delegation-1", []bus.MediaFile{{
		Path:     childImage,
		MimeType: "image/png",
		Filename: "image.png",
	}})
	if len(staged) != 1 {
		t.Fatalf("staged media count = %d, want 1", len(staged))
	}
	if staged[0].Path == childImage {
		t.Fatalf("media was not staged into parent workspace")
	}
	if !isPathInside(staged[0].Path, parentWorkspace) {
		t.Fatalf("staged path %q is outside parent workspace %q", staged[0].Path, parentWorkspace)
	}

	messageTool := NewMessageTool(parentWorkspace, true)
	mb := bus.New()
	messageTool.SetMessageBus(mb)
	msgCtx := WithToolWorkspace(context.Background(), parentWorkspace)
	msgCtx = WithToolSessionKey(msgCtx, "agent:test:cron:job")
	res := messageTool.Execute(msgCtx, map[string]any{
		"action":  "send",
		"channel": "discord",
		"target":  "channel-1",
		"message": "Article\nMEDIA:" + staged[0].Path,
	})
	if res == nil || res.IsError {
		t.Fatalf("message with staged media failed: %+v", res)
	}
	out := drainBusNow(mb)
	if len(out) != 1 {
		t.Fatalf("outbound count = %d, want 1", len(out))
	}
	if len(out[0].Media) != 1 {
		t.Fatalf("media attachment count = %d, want 1", len(out[0].Media))
	}
}

func TestDelegateSyncResultIncludesStagedMediaRefs(t *testing.T) {
	parentWorkspace := t.TempDir()
	childWorkspace := t.TempDir()
	childImage := filepath.Join(childWorkspace, "image.png")
	if err := os.WriteFile(childImage, []byte("png"), 0o600); err != nil {
		t.Fatal(err)
	}

	tool := &DelegateTool{runFn: func(context.Context, DelegateRequest) (DelegateResult, error) {
		return DelegateResult{
			Content: "DESIGN_STATUS: COMPLETE",
			Media:   []bus.MediaFile{{Path: childImage, MimeType: "image/png", Filename: "image.png"}},
		}, nil
	}}
	ctx := WithToolWorkspace(context.Background(), parentWorkspace)
	res := tool.executeSyncMode(ctx, DelegateRequest{DelegationID: "delegation-1", ToAgentKey: "cf-designer"}, 10, false)
	if res == nil || res.IsError {
		t.Fatalf("sync delegate failed: %+v", res)
	}

	var decoded struct {
		Media []struct {
			Path     string `json:"path"`
			MediaRef string `json:"media_ref"`
		} `json:"media"`
	}
	if err := json.Unmarshal([]byte(res.ForLLM), &decoded); err != nil {
		t.Fatal(err)
	}
	if len(decoded.Media) != 1 || decoded.Media[0].MediaRef == "" {
		t.Fatalf("media_ref missing from payload: %s", res.ForLLM)
	}
	if !isPathInside(decoded.Media[0].Path, parentWorkspace) {
		t.Fatalf("media path %q is outside parent workspace %q", decoded.Media[0].Path, parentWorkspace)
	}
}
