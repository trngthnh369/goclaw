package tools

import (
	"context"
	"os"
	"path/filepath"
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
	workspace := t.TempDir()
	tool := NewMessageTool(workspace, false)
	tool.SetMessageBus(mb)

	ctx := contentFactoryWakeCtx()
	args := contentFactoryDraftArgs(t, workspace, "approved article")

	res := tool.Execute(ctx, args)
	if res == nil || res.IsError {
		t.Fatalf("first send failed: %+v", res)
	}
	res = tool.Execute(ctx, contentFactoryDraftArgs(t, workspace, "different article should not send"))
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
	workspace := t.TempDir()
	tool := NewMessageTool(workspace, false)
	tool.SetMessageBus(mb)
	ctx := contentFactoryWakeCtx()
	args := contentFactoryDraftArgs(t, workspace, "approved article")

	var wg sync.WaitGroup
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			res := tool.Execute(ctx, args)
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
	workspace := t.TempDir()
	tool := NewMessageTool(workspace, false)
	tool.SetMessageBus(mb)

	res := tool.Execute(contentFactoryWakeCtx(), contentFactoryDraftArgs(t, workspace, "approved article"))
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

// contentFactoryDraftArgs builds a review draft the way a real one arrives:
// the article text and its audited image in ONE message.
func contentFactoryDraftArgs(t *testing.T, workspace, message string) map[string]any {
	t.Helper()
	img := filepath.Join(workspace, "draft.png")
	if err := os.WriteFile(img, []byte("png-data"), 0o600); err != nil {
		t.Fatal(err)
	}
	canonical, err := filepath.EvalSymlinks(img)
	if err != nil {
		t.Fatal(err)
	}
	return contentFactoryTerminalArgs(message + "\nMEDIA:" + canonical)
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

// A review draft is flagged so the Discord channel marks it approvable; an
// abort notice to the same channel is not something anyone should approve.
func TestMessageContentFactoryReviewDraftIsFlagged(t *testing.T) {
	for name, tc := range map[string]struct {
		message   string
		wantFlag  bool
		withImage bool
	}{
		"draft":        {message: "Bài viết đã audit.", wantFlag: true, withImage: true},
		"abort notice": {message: "[ABORT PIPELINE] Không đủ nguồn.", wantFlag: false},
	} {
		t.Run(name, func(t *testing.T) {
			workspace := t.TempDir()
			mb := bus.New()
			tool := NewMessageTool(workspace, false)
			tool.SetMessageBus(mb)
			var sent []bus.OutboundMessage
			tool.SetChannelSender(func(_ context.Context, channel, chatID, content string) error {
				sent = append(sent, bus.OutboundMessage{Channel: channel, ChatID: chatID, Content: content})
				return nil
			})
			tool.SetOutboundDispatcher(func(_ context.Context, msg bus.OutboundMessage) error {
				sent = append(sent, msg)
				return nil
			})

			args := contentFactoryTerminalArgs(tc.message)
			if tc.withImage {
				args = contentFactoryDraftArgs(t, workspace, tc.message)
			}
			res := tool.Execute(contentFactoryWakeCtx(), args)
			if res == nil || res.IsError {
				t.Fatalf("send failed: %+v", res)
			}
			// A draft goes out through the bus (it carries media); an abort
			// notice is text and takes the synchronous dispatcher.
			sent = append(sent, drainBusNow(mb)...)
			if len(sent) != 1 || sent[0].Content != tc.message {
				t.Fatalf("sent = %+v, want exactly the message once", sent)
			}
			if got := sent[0].Metadata[MetaContentFactoryReviewDraft] == "true"; got != tc.wantFlag {
				t.Fatalf("review draft flag = %v, want %v", got, tc.wantFlag)
			}
		})
	}
}

// An image that arrives after the draft can never join it: approval binds the
// text and image of one message. A draft with no image is refused outright,
// and the refusal must leave the one-shot terminal latch alone so the director
// can resend once cf-designer has produced the image.
func TestMessageContentFactoryDraftWithoutImageIsRefused(t *testing.T) {
	mb := bus.New()
	tool := NewMessageTool(t.TempDir(), false)
	tool.SetMessageBus(mb)

	latch := NewOutboundActionLatch()
	ctx := WithOutboundActionLatch(context.Background(), latch)
	ctx = WithToolSessionKey(ctx, "e2e-cf-noimage")
	ctx = WithToolChannel(ctx, "wake")
	ctx = WithToolChatID(ctx, "api")

	res := tool.Execute(ctx, contentFactoryTerminalArgs("Bài viết đã audit nhưng thiếu ảnh."))
	if res == nil || !res.IsError {
		t.Fatalf("text-only review draft must be refused, got: %+v", res)
	}
	if !strings.Contains(res.ForLLM, "MEDIA:") {
		t.Fatalf("refusal must say how to fix it, got: %q", res.ForLLM)
	}
	if got := drainBusNow(mb); len(got) != 0 {
		t.Fatalf("nothing may be sent: %+v", got)
	}
	if !latch.TryReserve(contentFactoryTerminalKey) {
		t.Fatal("refused draft must not consume the one-shot terminal latch")
	}
}

// An abort notice is how a run ends when no image can be produced, so it must
// still go through without one.
func TestMessageContentFactoryAbortNoticeNeedsNoImage(t *testing.T) {
	mb := bus.New()
	tool := NewMessageTool(t.TempDir(), false)
	tool.SetMessageBus(mb)

	res := tool.Execute(contentFactoryWakeCtx(), contentFactoryTerminalArgs("[ABORT PIPELINE] Không tạo được ảnh sau 2 lần thử."))
	if res == nil || res.IsError {
		t.Fatalf("abort notice failed: %+v", res)
	}
	if got := drainBusNow(mb); len(got) != 1 {
		t.Fatalf("outbound count = %d, want 1", len(got))
	}
}

// Every abort notice this pipeline has actually sent was worded freely and
// simply said "Abort". The gate must recognise those, or a run that cannot
// produce an image ends with nothing in the review channel.
func TestMessageContentFactoryRealAbortNoticeWordings(t *testing.T) {
	notices := []string{
		"⚠️ **ContentFactory SEED — Abort Notice**\n\nTopic: Bonsai 2 27B — audit không PASS.",
		"**ContentFactory Abort Notice**\n\nTopic: Salesforce DarwinX — không đủ nguồn.",
		"⚠️ ABORT — Moderna AI Cancer Vaccine\n\nAudit 2 vòng đều trả UNCERTAIN.",
		"[ABORT PIPELINE] Không tạo được ảnh.",
	}
	for _, notice := range notices {
		mb := bus.New()
		tool := NewMessageTool(t.TempDir(), false)
		tool.SetMessageBus(mb)

		res := tool.Execute(contentFactoryWakeCtx(), contentFactoryTerminalArgs(notice))
		if res == nil || res.IsError {
			t.Fatalf("abort notice %q refused: %+v", notice[:20], res)
		}
		if got := drainBusNow(mb); len(got) != 1 {
			t.Fatalf("abort notice %q: outbound = %d, want 1", notice[:20], len(got))
		}
	}
}
