package agent

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/nextlevelbuilder/goclaw/internal/pipeline"
	"github.com/nextlevelbuilder/goclaw/internal/providers"
	"github.com/nextlevelbuilder/goclaw/internal/tools"
)

// scriptedProvider records every request it receives and replays canned
// responses in order, falling back to a text-only reply once the script runs
// out (the model that keeps narrating).
type scriptedProvider struct {
	calls []providers.ChatRequest
	resps []*providers.ChatResponse
}

func (p *scriptedProvider) Chat(_ context.Context, req providers.ChatRequest) (*providers.ChatResponse, error) {
	p.calls = append(p.calls, req)
	if len(p.calls) <= len(p.resps) {
		return p.resps[len(p.calls)-1], nil
	}
	return &providers.ChatResponse{Content: "Đang yêu cầu cf-auditor kiểm duyệt lại."}, nil
}

func (p *scriptedProvider) ChatStream(ctx context.Context, req providers.ChatRequest, _ func(providers.StreamChunk)) (*providers.ChatResponse, error) {
	return p.Chat(ctx, req)
}

func (p *scriptedProvider) DefaultModel() string { return "test-model" }
func (p *scriptedProvider) Name() string         { return "test-provider" }

func narrationOnly() *providers.ChatResponse {
	// Exactly the shape that ended the 2026-08-05 cron run: prose describing the
	// next delegation, no tool call, so the pipeline breaks the loop.
	return &providers.ChatResponse{
		Content:      "Đang yêu cầu `cf-auditor` kiểm duyệt lại (revision 2). Tôi sẽ truyền nguyên văn bài viết.",
		FinishReason: "stop",
	}
}

func withDirectorLatch(reserved bool) (context.Context, *tools.OutboundActionLatch) {
	latch := tools.NewOutboundActionLatch()
	if reserved {
		// Mirrors what message.go does when the review draft is sent.
		latch.TryReserve("contentfactory-terminal")
	}
	return tools.WithOutboundActionLatch(context.Background(), latch), latch
}

// cronRun mirrors what cmd/gateway_cron.go builds: the RunID prefix is the only
// thing distinguishing it from a human chatting on the same channel, because
// cron_jobs.deliver_channel for this job IS the review channel.
func cronRun() *RunRequest {
	return &RunRequest{RunID: "cron:job-1", SessionKey: "sess-1", Channel: "cf-discord"}
}

func callOnce(t *testing.T, ctx context.Context, agentKey string, req *RunRequest, prov *scriptedProvider) {
	t.Helper()
	col := &eventCollector{}
	loop := &Loop{id: agentKey, onEvent: col.onEvent}
	state := &pipeline.RunState{Provider: prov, Model: "test-model"}
	if _, err := loop.makeCallLLM(req, col.onEvent)(ctx, state, providers.ChatRequest{}); err != nil {
		t.Fatalf("makeCallLLM: %v", err)
	}
}

func TestIsPipelineRun(t *testing.T) {
	tests := []struct {
		name string
		req  *RunRequest
		want bool
	}{
		{"cron run", &RunRequest{RunID: "cron:job-1", Channel: "cf-discord"}, true},
		{"wake run", &RunRequest{RunID: "abc-123", Channel: "wake"}, true},
		{"human chat on the review channel", &RunRequest{RunID: "abc-123", Channel: "cf-discord"}, false},
		{"telegram chat", &RunRequest{RunID: "abc-123", Channel: "telegram"}, false},
		{"nil request", nil, false},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := isPipelineRun(tt.req); got != tt.want {
				t.Errorf("isPipelineRun = %v, want %v", got, tt.want)
			}
		})
	}
}

// The director also holds an ordinary conversation on the review channel. A
// person asking it a question must get a prose answer, not a forced tool call.
func TestMakeCallLLM_NoNudgeForInteractiveChat(t *testing.T) {
	ctx, _ := withDirectorLatch(false)
	prov := &scriptedProvider{resps: []*providers.ChatResponse{narrationOnly()}}
	chat := &RunRequest{RunID: "abc-123", SessionKey: "sess-1", Channel: "cf-discord"}

	callOnce(t, ctx, "cf-director", chat, prov)

	if len(prov.calls) != 1 {
		t.Fatalf("provider calls = %d, want 1 (a human conversation is never nudged)", len(prov.calls))
	}
}

// The director narrating instead of acting must be pushed to emit the tool call
// rather than ending the run — this is the exact failure that produced a
// status=ok run with no review message on 2026-08-02 and 2026-08-05.
func TestMakeCallLLM_NudgesDirectorWhenTerminalActionPending(t *testing.T) {
	ctx, _ := withDirectorLatch(false)
	prov := &scriptedProvider{resps: []*providers.ChatResponse{
		narrationOnly(),
		{ToolCalls: []providers.ToolCall{{ID: "1", Name: "delegate"}}},
	}}

	callOnce(t, ctx, "cf-director", cronRun(), prov)

	if len(prov.calls) != 2 {
		t.Fatalf("provider calls = %d, want 2 (initial + nudged retry)", len(prov.calls))
	}
	retry := prov.calls[1]
	if got := retry.Options[providers.OptToolChoice]; got != "required" {
		t.Errorf("retry tool_choice = %v, want required", got)
	}
	if len(retry.Messages) == 0 {
		t.Fatal("retry carried no messages")
	}
	last := retry.Messages[len(retry.Messages)-1]
	if last.Role != "system" || !strings.Contains(last.Content, "narration") {
		t.Errorf("retry nudge = %q (role %q), want a system message about narration", last.Content, last.Role)
	}
}

// Once the review draft is out, the run is allowed to end on prose.
func TestMakeCallLLM_NoNudgeAfterTerminalActionDone(t *testing.T) {
	ctx, _ := withDirectorLatch(true)
	prov := &scriptedProvider{resps: []*providers.ChatResponse{narrationOnly()}}

	callOnce(t, ctx, "cf-director", cronRun(), prov)

	if len(prov.calls) != 1 {
		t.Fatalf("provider calls = %d, want 1 (no nudge after the terminal action)", len(prov.calls))
	}
}

// The guard sits on the shared run path, so every other agent must be able to
// end a turn with a plain text answer.
func TestMakeCallLLM_NoNudgeForOtherAgents(t *testing.T) {
	ctx, _ := withDirectorLatch(false)
	prov := &scriptedProvider{resps: []*providers.ChatResponse{narrationOnly()}}

	callOnce(t, ctx, "some-other-agent", cronRun(), prov)

	if len(prov.calls) != 1 {
		t.Fatalf("provider calls = %d, want 1 (unrelated agents are never nudged)", len(prov.calls))
	}
}

// errorOnRetryProvider succeeds once, then fails — the shape of a provider that
// rejects tool_choice=required.
type errorOnRetryProvider struct {
	calls int
}

func (p *errorOnRetryProvider) Chat(context.Context, providers.ChatRequest) (*providers.ChatResponse, error) {
	p.calls++
	if p.calls == 1 {
		return narrationOnly(), nil
	}
	return nil, errors.New("tool_choice not supported")
}

func (p *errorOnRetryProvider) ChatStream(ctx context.Context, req providers.ChatRequest, _ func(providers.StreamChunk)) (*providers.ChatResponse, error) {
	return p.Chat(ctx, req)
}
func (p *errorOnRetryProvider) DefaultModel() string { return "test-model" }
func (p *errorOnRetryProvider) Name() string         { return "test-provider" }

// A failed nudge must not be worse than no nudge: the original answer stands and
// the run continues, rather than the whole run erroring out on the rescue attempt.
func TestMakeCallLLM_FailedNudgeKeepsOriginalResponse(t *testing.T) {
	ctx, _ := withDirectorLatch(false)
	prov := &errorOnRetryProvider{}
	col := &eventCollector{}
	loop := &Loop{id: "cf-director", onEvent: col.onEvent}
	state := &pipeline.RunState{Provider: prov, Model: "test-model"}

	resp, err := loop.makeCallLLM(cronRun(), col.onEvent)(ctx, state, providers.ChatRequest{})
	if err != nil {
		t.Fatalf("makeCallLLM returned the retry error: %v", err)
	}
	if resp == nil || !strings.Contains(resp.Content, "kiểm duyệt lại") {
		t.Fatalf("response = %+v, want the original narration preserved", resp)
	}
	if prov.calls != 2 {
		t.Errorf("provider calls = %d, want 2 (initial + failed retry)", prov.calls)
	}
}

// A model that ignores the nudge must not be retried forever: each nudge costs a
// full extra LLM call, and the run has to be allowed to end (and be reported) instead.
func TestMakeCallLLM_NudgeBudgetIsBounded(t *testing.T) {
	ctx, _ := withDirectorLatch(false)
	prov := &scriptedProvider{} // always narrates, never complies
	col := &eventCollector{}
	loop := &Loop{id: "cf-director", onEvent: col.onEvent}
	req := cronRun()
	state := &pipeline.RunState{Provider: prov, Model: "test-model"}
	callLLM := loop.makeCallLLM(req, col.onEvent)

	// One iteration past the budget, so the last one must go unnudged. Derived
	// from the constant: hardcoding the count makes this fail spuriously the
	// moment the budget changes, which reads as a regression it did not catch.
	iterations := maxTerminalActionNudges + 1
	for i := 0; i < iterations; i++ {
		if _, err := callLLM(ctx, state, providers.ChatRequest{}); err != nil {
			t.Fatalf("iteration %d: %v", i, err)
		}
	}

	// Each nudged iteration costs 2 provider calls, the final one costs 1.
	want := 2*maxTerminalActionNudges + 1
	if len(prov.calls) != want {
		t.Fatalf("provider calls = %d, want %d (%d nudges max per run)", len(prov.calls), want, maxTerminalActionNudges)
	}
}
