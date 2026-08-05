package tools

import (
	"context"
	"strings"
	"sync"
	"testing"

	"github.com/nextlevelbuilder/goclaw/internal/bus"
)

func TestMessageContentFactoryTerminalRequiresIdempotencyKey(t *testing.T) {
	tool := NewMessageTool(t.TempDir(), false)
	tool.SetMessageBus(bus.New())

	ctx := context.Background()
	ctx = WithToolSessionKey(ctx, "e2e-cf-test")
	ctx = WithToolChannel(ctx, "wake")
	ctx = WithToolChatID(ctx, "api")

	res := tool.Execute(ctx, map[string]any{
		"action":         "send",
		"channel":        "cf-discord",
		"target":         contentFactoryApprovalChannelID,
		"forward":        true,
		"forward_reason": "test forward",
		"message":        "approved article",
	})
	if res == nil || !res.IsError {
		t.Fatalf("expected missing idempotency key to fail, got %+v", res)
	}
}

// A review draft carrying an image is delivered as one media message and fails
// closed above the platform limit. Rejecting it after the latch was reserved
// would end the run with no review message and no way to retry, so the size
// check must run first and leave the latch untouched.
func TestMessageContentFactoryOversizeReviewDoesNotBurnLatch(t *testing.T) {
	tool := NewMessageTool(t.TempDir(), false)
	tool.SetMessageBus(bus.New())

	latch := NewOutboundActionLatch()
	ctx := WithOutboundActionLatch(context.Background(), latch)
	ctx = WithToolSessionKey(ctx, "e2e-cf-oversize")
	ctx = WithToolChannel(ctx, "wake")
	ctx = WithToolChatID(ctx, "api")

	oversize := strings.Repeat("a", maxReviewMessageBytes+1) + "\nMEDIA:/tmp/x.png"
	res := tool.Execute(ctx, map[string]any{
		"action":          "send",
		"channel":         "cf-discord",
		"target":          contentFactoryApprovalChannelID,
		"forward":         true,
		"forward_reason":  "test forward",
		"idempotency_key": contentFactoryTerminalKey,
		"message":         oversize,
	})
	if res == nil || !res.IsError {
		t.Fatalf("oversize review draft with media should be rejected, got %+v", res)
	}
	if !latch.TryReserve(contentFactoryTerminalKey) {
		t.Fatal("rejected send must not consume the one-shot terminal latch")
	}
}

func TestMessageContentFactoryTextOnlyReviewIsNotSizeLimited(t *testing.T) {
	if n := reviewTextBytesWithMedia(strings.Repeat("a", maxReviewMessageBytes+500)); n != 0 {
		t.Errorf("text-only drafts are chunked by the adapter and must not be size-gated here, got %d", n)
	}
	withMedia := strings.Repeat("a", 10) + "\nMEDIA:/tmp/x.png"
	if n := reviewTextBytesWithMedia(withMedia); n != 10 {
		t.Errorf("media path must be excluded from the measured text, got %d want 10", n)
	}
}

func TestMessageContentFactoryTerminalSuppressesDuplicates(t *testing.T) {
	mb := bus.New()
	tool := NewMessageTool(t.TempDir(), false)
	tool.SetMessageBus(mb)

	ctx := contentFactoryWakeCtx()
	args := contentFactoryTerminalArgs("approved article")

	res := tool.Execute(ctx, args)
	if res == nil || res.IsError {
		t.Fatalf("first send failed: %+v", res)
	}
	res = tool.Execute(ctx, contentFactoryTerminalArgs("different article should not send"))
	if res == nil || res.IsError {
		t.Fatalf("duplicate should be suppressed without error: %+v", res)
	}

	got := drainBusNow(mb)
	if len(got) != 1 {
		t.Fatalf("outbound count = %d, want 1: %+v", len(got), got)
	}
	if got[0].Content != "approved article" {
		t.Fatalf("content = %q, want first article", got[0].Content)
	}
}

func TestMessageContentFactoryTerminalSuppressesConcurrentDuplicates(t *testing.T) {
	mb := bus.New()
	tool := NewMessageTool(t.TempDir(), false)
	tool.SetMessageBus(mb)
	ctx := contentFactoryWakeCtx()

	var wg sync.WaitGroup
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			res := tool.Execute(ctx, contentFactoryTerminalArgs("approved article"))
			if res == nil || res.IsError {
				t.Errorf("send failed: %+v", res)
			}
		}()
	}
	wg.Wait()

	got := drainBusNow(mb)
	if len(got) != 1 {
		t.Fatalf("outbound count = %d, want 1: %+v", len(got), got)
	}
}

func TestMessageContentFactoryWakeSkipsCrossTargetBreadcrumb(t *testing.T) {
	mb := bus.New()
	tool := NewMessageTool(t.TempDir(), false)
	tool.SetMessageBus(mb)

	res := tool.Execute(contentFactoryWakeCtx(), contentFactoryTerminalArgs("approved article"))
	if res == nil || res.IsError {
		t.Fatalf("send failed: %+v", res)
	}
	got := drainBusNow(mb)
	if len(got) != 1 {
		t.Fatalf("outbound count = %d, want only target send without wake breadcrumb: %+v", len(got), got)
	}
	if got[0].Channel != "cf-discord" || got[0].ChatID != contentFactoryApprovalChannelID {
		t.Fatalf("unexpected target outbound: %+v", got[0])
	}
}

func contentFactoryWakeCtx() context.Context {
	ctx := context.Background()
	ctx = WithToolSessionKey(ctx, "e2e-cf-test")
	ctx = WithToolChannel(ctx, "wake")
	ctx = WithToolChatID(ctx, "api")
	ctx = WithOutboundActionLatch(ctx, NewOutboundActionLatch())
	return ctx
}

func contentFactoryTerminalArgs(message string) map[string]any {
	return map[string]any{
		"action":          "send",
		"channel":         "cf-discord",
		"target":          contentFactoryApprovalChannelID,
		"forward":         true,
		"forward_reason":  "test forward",
		"idempotency_key": contentFactoryTerminalKey,
		"message":         message,
	}
}
