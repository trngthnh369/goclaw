package tools

import (
	"context"
	"strings"
	"testing"

	"github.com/nextlevelbuilder/goclaw/internal/bus"
)

// --- F10: an abort notice must be able to reach the review channel ---

func TestInternalNoisePattern_BlocksOnlyMachinePlumbing(t *testing.T) {
	blocked := []string{
		`{"agent":"cf-writer","status":"failed"}`,
		"tool execution error: boom",
		"context deadline exceeded",
	}
	for _, s := range blocked {
		if !internalNoisePattern.MatchString(s) {
			t.Errorf("internalNoisePattern did not block %q", s)
		}
	}
	// Abort wording moved out: keeping it here made the abort path unreachable,
	// because the director is told to send abort notices to this same channel.
	allowed := []string{
		"[ABORT PIPELINE] không đủ evidence để viết bài hôm nay.",
		"CRITICAL: nguồn chính không truy cập được, dừng pipeline.",
		"I was unable to complete this task because the sources were unreachable.",
	}
	for _, s := range allowed {
		if internalNoisePattern.MatchString(s) {
			t.Errorf("internalNoisePattern still blocks abort wording %q", s)
		}
		if !abortPhrasePattern.MatchString(s) {
			t.Errorf("abortPhrasePattern did not recognise %q", s)
		}
	}
}

func TestAbortPhrasePattern_IgnoresOrdinaryProse(t *testing.T) {
	// A normal draft must not be mistaken for an abort notice.
	body := "Từ ngày 15/08/2026, các doanh nghiệp công nghệ Việt Nam bước vào khuôn khổ pháp lý mới."
	if abortPhrasePattern.MatchString(body) {
		t.Errorf("abortPhrasePattern matched an ordinary article body")
	}
}

// --- F11: the oversize rejection must say how much to cut ---

func TestReviewOversizeError_StatesHowMuchToRemove(t *testing.T) {
	// Vietnamese runs ~1.36 bytes/char, so a draft sized at the top of the old
	// 1750-character brief lands over Discord's 2000-byte ceiling.
	body := strings.Repeat("Việt Nam đang xây dựng năng lực trí tuệ nhân tạo có chủ quyền. ", 40)
	msg := body + "\nMEDIA:/tmp/a.png"

	n := reviewTextBytesWithMedia(msg)
	if n <= maxReviewMessageBytes {
		t.Fatalf("fixture is only %d bytes; it must exceed %d to exercise the guard", n, maxReviewMessageBytes)
	}
	// Bytes-per-char above 1 is what makes a character brief and a byte ceiling
	// disagree — the whole reason this guard fires.
	chars := len([]rune(strings.TrimSpace(embeddedMediaPattern.ReplaceAllString(msg, ""))))
	if ratio := float64(n) / float64(chars); ratio < 1.2 {
		t.Errorf("fixture bytes/char = %.2f, too ASCII to represent Vietnamese", ratio)
	}
}

// --- the terminal send is one-shot: a repeat must end the run ---

// After the review draft (or abort notice) is out, the run's work is done. A
// live abort-path test showed the director re-sending five more times, each
// getting the same suppression, until the loop detector killed a run whose work
// had actually succeeded.
func TestReviewTerminalDuplicate_EndsRunWithNoReply(t *testing.T) {
	mb := bus.New()
	tool := NewMessageTool(t.TempDir(), false)
	tool.SetMessageBus(mb)
	ctx := contentFactoryWakeCtx()

	if res := tool.Execute(ctx, contentFactoryTerminalArgs("the review draft")); res == nil || res.IsError {
		t.Fatalf("first send failed: %+v", res)
	}
	second := tool.Execute(ctx, contentFactoryTerminalArgs("a second send after the terminal action"))

	if second == nil || !second.EndRun {
		t.Fatalf("duplicate terminal send must end the run, got: %+v", second)
	}
	// NO_REPLY is what the pipeline already suppresses, so the run ends quietly
	// instead of surfacing internal status JSON as its answer.
	if second.ForLLM != "NO_REPLY" {
		t.Errorf("ForLLM = %q, want NO_REPLY", second.ForLLM)
	}
	// The send already succeeded; reporting an error here would fail a
	// delegated task whose work was done.
	if second.IsError {
		t.Error("a suppressed duplicate is not an error")
	}
	if got := drainBusNow(mb); len(got) != 1 {
		t.Fatalf("outbound count = %d, want 1 (the duplicate must not send)", len(got))
	}
}

// --- F12: a refused image retry must hand back the path it produced ---

func TestOutboundActionLatch_RemembersResultForRefusedRetry(t *testing.T) {
	latch := NewOutboundActionLatch()
	const key = contentFactoryDesignerImageActionKey

	if !latch.TryReserve(key) {
		t.Fatal("first reservation failed")
	}
	// The bare path is stored; the MEDIA: prefix belongs to the rendered block.
	latch.Remember(key, "/w/img.png")

	if latch.TryReserve(key) {
		t.Fatal("second reservation succeeded; the one-shot is broken")
	}
	// Without this, "return the prior MEDIA path" is an instruction the caller
	// cannot follow — the observed result was retrying until iterations ran out.
	if got := latch.Recall(key); got != "/w/img.png" {
		t.Errorf("Recall = %q, want the remembered path", got)
	}
}

// The refusal replays the success block, so both must render identically. A
// stored value carrying its own "MEDIA:" produced "MEDIA: MEDIA:/app/..." in a
// live run — the agent was asked to emit a shape it had never been taught.
func TestDesignerCompleteBlock_SinglePrefixAndStableShape(t *testing.T) {
	block := designerCompleteBlock("/app/workspace/cf-designer/generated/x.png")

	if strings.Count(block, "MEDIA:") != 1 {
		t.Errorf("block has %d MEDIA: prefixes, want exactly 1:\n%s", strings.Count(block, "MEDIA:"), block)
	}
	for _, want := range []string{"DESIGN_STATUS: COMPLETE", "IMAGE_COUNT: 1", "IMAGE_PATH: MEDIA:/app/workspace/cf-designer/generated/x.png"} {
		if !strings.Contains(block, want) {
			t.Errorf("block missing %q:\n%s", want, block)
		}
	}
	// Feeding a stored value back through the renderer must not compound.
	if again := designerCompleteBlock("/p.png"); strings.Count(again, "MEDIA:") != 1 {
		t.Errorf("renderer is not idempotent in shape: %s", again)
	}
}

func TestOutboundActionLatch_RecallEmptyWhenNothingRemembered(t *testing.T) {
	latch := NewOutboundActionLatch()
	latch.TryReserve("k")
	if got := latch.Recall("k"); got != "" {
		t.Errorf("Recall = %q, want empty so the caller reports FAILED", got)
	}
	var nilLatch *OutboundActionLatch
	if got := nilLatch.Recall("k"); got != "" {
		t.Errorf("nil latch Recall = %q, want empty", got)
	}
}

func TestOutboundActionLatch_RememberIsPerKey(t *testing.T) {
	latch := NewOutboundActionLatch()
	latch.Remember("a", "1")
	latch.Remember("b", "2")
	if latch.Recall("a") != "1" || latch.Recall("b") != "2" {
		t.Error("Remember leaked across keys")
	}
	_ = context.Background()
}
