package tools

import (
	"context"
	"testing"
)

// Reserved must not consume the one-shot it reports on. A probe implemented
// with TryReserve would return "not yet done" once and then silently mark the
// terminal action as done, disarming the real dedup in message.go.
func TestOutboundActionLatch_ReservedDoesNotMutate(t *testing.T) {
	latch := NewOutboundActionLatch()

	if latch.Reserved("k") {
		t.Fatal("Reserved = true on a fresh latch, want false")
	}
	// Probing repeatedly must leave the reservation available.
	for i := 0; i < 3; i++ {
		_ = latch.Reserved("k")
	}
	if !latch.TryReserve("k") {
		t.Fatal("TryReserve = false after probing; Reserved consumed the one-shot")
	}
	if !latch.Reserved("k") {
		t.Fatal("Reserved = false after TryReserve, want true")
	}
	if latch.TryReserve("k") {
		t.Fatal("TryReserve = true on second call, want false")
	}
}

func TestOutboundActionLatch_ReservedNilAndEmpty(t *testing.T) {
	var nilLatch *OutboundActionLatch
	if nilLatch.Reserved("k") {
		t.Error("nil latch Reserved = true, want false")
	}
	if NewOutboundActionLatch().Reserved("") {
		t.Error("empty key Reserved = true, want false")
	}
}

func TestContentFactoryTerminalActionPending(t *testing.T) {
	tests := []struct {
		name     string
		agentKey string
		withCtx  bool
		reserve  bool
		want     bool
	}{
		{
			name:     "director before terminal action is pending",
			agentKey: contentFactoryDirectorAgentKey,
			withCtx:  true,
			want:     true,
		},
		{
			name:     "director after terminal action is not pending",
			agentKey: contentFactoryDirectorAgentKey,
			withCtx:  true,
			reserve:  true,
			want:     false,
		},
		{
			// Every other agent must be unaffected: the check runs on the shared
			// run path, so a wrong answer here would nudge unrelated agents.
			name:     "non-director agent is never pending",
			agentKey: "some-other-agent",
			withCtx:  true,
			want:     false,
		},
		{
			name:     "designer is not the terminal-action owner",
			agentKey: contentFactoryDesignerAgentKey,
			withCtx:  true,
			want:     false,
		},
		{
			// Fail quiet, not loud: without a latch there is nothing to observe,
			// so claiming "pending" would nudge on every run of every agent.
			name:     "no latch in context is not pending",
			agentKey: contentFactoryDirectorAgentKey,
			withCtx:  false,
			want:     false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			ctx := context.Background()
			if tt.withCtx {
				latch := NewOutboundActionLatch()
				if tt.reserve {
					latch.TryReserve(contentFactoryTerminalKey)
				}
				ctx = WithOutboundActionLatch(ctx, latch)
			}
			if got := ContentFactoryTerminalActionPending(ctx, tt.agentKey); got != tt.want {
				t.Errorf("ContentFactoryTerminalActionPending = %v, want %v", got, tt.want)
			}
		})
	}
}

// The pending check must observe the exact key message.go reserves. If the two
// drift apart the guard reports "pending" forever and every director run gets
// nudged even after the review draft was sent.
func TestContentFactoryTerminalActionPending_UsesMessageToolKey(t *testing.T) {
	latch := NewOutboundActionLatch()
	ctx := WithOutboundActionLatch(context.Background(), latch)

	if !ContentFactoryTerminalActionPending(ctx, contentFactoryDirectorAgentKey) {
		t.Fatal("want pending before the review send")
	}
	// Same call message.go makes when a review draft reaches the review channel.
	if !latch.TryReserve(contentFactoryTerminalKey) {
		t.Fatal("TryReserve failed on a fresh latch")
	}
	if ContentFactoryTerminalActionPending(ctx, contentFactoryDirectorAgentKey) {
		t.Fatal("still pending after the review send was reserved")
	}
}
