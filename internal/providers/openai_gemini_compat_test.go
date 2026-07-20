package providers

import (
	"encoding/json"
	"strings"
	"testing"
)

// TestBuildRequestBody_GeminiCompatCollapseToolCalls verifies that a provider
// with geminiCompat=true collapses tool_call cycles missing thought_signature,
// exactly like a native Gemini provider would.
func TestBuildRequestBody_GeminiCompatCollapseToolCalls(t *testing.T) {
	p := NewOpenAIProvider("cliproxy", "key", "http://cliproxy:8317/v1", "ag-pro").
		WithProviderType("openrouter").
		WithGeminiCompat()

	msgs := []Message{
		{Role: "user", Content: "hello"},
		{Role: "assistant", Content: "", ToolCalls: []ToolCall{
			{ID: "tc1", Name: "web_search", Arguments: map[string]any{"q": "test"}},
		}},
		{Role: "tool", Content: "search result here", ToolCallID: "tc1"},
		{Role: "user", Content: "thanks"},
	}
	req := ChatRequest{Messages: msgs}
	body := p.buildRequestBody("ag-pro", req, false)

	rawMsgs, _ := json.Marshal(body["messages"])
	wireStr := string(rawMsgs)

	// After collapse, the tool_calls assistant message should be stripped
	// and the tool result folded into a user message.
	if strings.Contains(wireStr, `"tool_calls"`) {
		t.Errorf("geminiCompat provider should collapse tool_calls without sig; got tool_calls in wire body")
	}
	if strings.Contains(wireStr, `"tool_call_id"`) {
		t.Errorf("geminiCompat provider should collapse tool results; got tool_call_id in wire body")
	}
}

// TestBuildRequestBody_GeminiCompatForwardsThoughtSig verifies that a
// geminiCompat provider includes thought_signature on echoed tool_calls.
func TestBuildRequestBody_GeminiCompatForwardsThoughtSig(t *testing.T) {
	p := NewOpenAIProvider("cliproxy", "key", "http://cliproxy:8317/v1", "ag-pro").
		WithProviderType("openrouter").
		WithGeminiCompat()

	msgs := []Message{
		{Role: "user", Content: "go"},
		{Role: "assistant", Content: "", ToolCalls: []ToolCall{
			{
				ID: "tc1", Name: "noop",
				Arguments: map[string]any{},
				Metadata:  map[string]string{"thought_signature": "sig-abc"},
			},
		}},
		{Role: "tool", Content: "ok", ToolCallID: "tc1"},
		{Role: "user", Content: "next"},
	}
	req := ChatRequest{Messages: msgs}
	body := p.buildRequestBody("ag-pro", req, false)

	rawMsgs, _ := json.Marshal(body["messages"])
	if !strings.Contains(string(rawMsgs), `"thought_signature":"sig-abc"`) {
		t.Errorf("geminiCompat provider must forward thought_signature; body=%s", rawMsgs)
	}
}

// TestBuildRequestBody_GeminiCompatReasoningEffort verifies that a
// geminiCompat provider forwards reasoning_effort from OptThinkingLevel.
func TestBuildRequestBody_GeminiCompatReasoningEffort(t *testing.T) {
	p := NewOpenAIProvider("cliproxy", "key", "http://cliproxy:8317/v1", "ag-pro").
		WithProviderType("openrouter").
		WithGeminiCompat()

	req := ChatRequest{
		Messages: []Message{{Role: "user", Content: "hi"}},
		Options:  map[string]any{OptThinkingLevel: "low"},
	}
	body := p.buildRequestBody("ag-pro", req, false)

	got, exists := body[OptReasoningEffort]
	if !exists {
		t.Fatalf("geminiCompat provider must forward reasoning_effort; body=%v", body)
	}
	if str, ok := got.(string); !ok || str != "low" {
		t.Fatalf("reasoning_effort = %v, want \"low\"", got)
	}
}

// TestBuildRequestBody_GeminiCompatToolResultName verifies that a
// geminiCompat provider includes the name field on role=tool messages.
func TestBuildRequestBody_GeminiCompatToolResultName(t *testing.T) {
	p := NewOpenAIProvider("cliproxy", "key", "http://cliproxy:8317/v1", "ag-pro").
		WithProviderType("openrouter").
		WithGeminiCompat()

	msgs := []Message{
		{Role: "user", Content: "go"},
		{Role: "assistant", Content: "", ToolCalls: []ToolCall{
			{
				ID: "tc1", Name: "web_search",
				Arguments: map[string]any{},
				Metadata:  map[string]string{"thought_signature": "sig-xyz"},
			},
		}},
		{Role: "tool", Content: "result", ToolCallID: "tc1"},
	}
	req := ChatRequest{Messages: msgs}
	body := p.buildRequestBody("ag-pro", req, false)

	rawMsgs, ok := body["messages"].([]map[string]any)
	if !ok {
		t.Fatalf("messages is not []map[string]any")
	}
	for _, m := range rawMsgs {
		if m["role"] == "tool" {
			if name, exists := m["name"]; !exists || name != "web_search" {
				t.Errorf("tool message must have name=web_search for geminiCompat; got name=%v exists=%v", name, exists)
			}
			return
		}
	}
	t.Fatal("no tool message found in wire body")
}

// TestBuildRequestBody_NonGeminiCompatUnaffected verifies that a provider
// with the same config but WITHOUT geminiCompat does NOT get Gemini behaviors.
func TestBuildRequestBody_NonGeminiCompatUnaffected(t *testing.T) {
	p := NewOpenAIProvider("cliproxy", "key", "http://cliproxy:8317/v1", "ag-pro").
		WithProviderType("openrouter")
	// Note: no WithGeminiCompat()

	t.Run("no_collapse", func(t *testing.T) {
		msgs := []Message{
			{Role: "user", Content: "hello"},
			{Role: "assistant", Content: "", ToolCalls: []ToolCall{
				{ID: "tc1", Name: "web_search", Arguments: map[string]any{"q": "test"}},
			}},
			{Role: "tool", Content: "result", ToolCallID: "tc1"},
		}
		body := p.buildRequestBody("ag-pro", ChatRequest{Messages: msgs}, false)
		rawMsgs, _ := json.Marshal(body["messages"])
		if !strings.Contains(string(rawMsgs), `"tool_calls"`) {
			t.Error("non-geminiCompat provider should preserve tool_calls")
		}
	})

	t.Run("no_thought_signature", func(t *testing.T) {
		msgs := []Message{
			{Role: "user", Content: "go"},
			{Role: "assistant", Content: "", ToolCalls: []ToolCall{
				{ID: "tc1", Name: "noop", Arguments: map[string]any{},
					Metadata: map[string]string{"thought_signature": "sig-abc"}},
			}},
			{Role: "tool", Content: "ok", ToolCallID: "tc1"},
		}
		body := p.buildRequestBody("ag-pro", ChatRequest{Messages: msgs}, false)
		rawMsgs, _ := json.Marshal(body["messages"])
		if strings.Contains(string(rawMsgs), `"thought_signature"`) {
			t.Error("non-geminiCompat provider should NOT forward thought_signature")
		}
	})

	t.Run("no_reasoning_effort", func(t *testing.T) {
		req := ChatRequest{
			Messages: []Message{{Role: "user", Content: "hi"}},
			Options:  map[string]any{OptThinkingLevel: "low"},
		}
		body := p.buildRequestBody("ag-pro", req, false)
		if _, exists := body[OptReasoningEffort]; exists {
			t.Error("non-geminiCompat provider should NOT forward reasoning_effort")
		}
	})
}
