package agent

import (
	"context"
	"testing"

	"github.com/nextlevelbuilder/goclaw/internal/providers"
)

// A channel that delivers answers whole (Discord) must still ask the provider
// for a streaming transport, or a long answer runs into the response-header
// timeout before the first byte arrives.
func TestMakeCallLLM_NonStreamRunRequestsStreamTransport(t *testing.T) {
	prov := &scriptedProvider{resps: []*providers.ChatResponse{{Content: "ok", FinishReason: "stop"}}}
	req := &RunRequest{RunID: "inbound-1", SessionKey: "sess-1", Channel: "discord-bot", Stream: false}

	callOnce(t, context.Background(), "zip-crazy", req, prov)

	if len(prov.calls) != 1 {
		t.Fatalf("provider calls = %d, want 1", len(prov.calls))
	}
	if got, _ := prov.calls[0].Options[providers.OptStreamTransport].(bool); !got {
		t.Fatalf("OptStreamTransport = %v, want true for a non-streaming run", prov.calls[0].Options[providers.OptStreamTransport])
	}
}

func TestMakeCallLLM_StreamRunLeavesTransportToChatStream(t *testing.T) {
	prov := &scriptedProvider{resps: []*providers.ChatResponse{{Content: "ok", FinishReason: "stop"}}}
	req := &RunRequest{RunID: "inbound-1", SessionKey: "sess-1", Channel: "telegram", Stream: true}

	callOnce(t, context.Background(), "zip-crazy", req, prov)

	if _, set := prov.calls[0].Options[providers.OptStreamTransport]; set {
		t.Fatal("OptStreamTransport set on a streaming run; ChatStream already streams")
	}
}
