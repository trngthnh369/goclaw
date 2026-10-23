package providers

import (
	"encoding/json"
	"fmt"
	"log/slog"
	"strings"
)

// toolCallAssembler rebuilds tool calls from streamed deltas in the order they
// first appear. Deltas are keyed by index, but upstreams disagree on what an
// index means: some start at 1 or skip numbers, and some proxies send every
// parallel call at index 0 with its own id. A new non-empty id at an index
// already holding a different call therefore starts a new call instead of
// gluing two argument strings into one invalid JSON blob.
type toolCallAssembler struct {
	order   []*toolCallAccumulator
	byIndex map[int]*toolCallAccumulator
}

func (a *toolCallAssembler) add(tc openAIStreamToolCall) {
	if a.byIndex == nil {
		a.byIndex = make(map[int]*toolCallAccumulator)
	}
	acc := a.byIndex[tc.Index]
	if acc == nil || (tc.ID != "" && acc.ID != "" && tc.ID != acc.ID) {
		acc = &toolCallAccumulator{}
		a.byIndex[tc.Index] = acc
		a.order = append(a.order, acc)
	}
	if acc.ID == "" {
		acc.ID = tc.ID
	}
	if name := strings.TrimSpace(tc.Function.Name); name != "" {
		acc.Name = name
	}
	acc.rawArgs += tc.Function.Arguments
	if tc.Function.ThoughtSignature != "" {
		acc.thoughtSig = tc.Function.ThoughtSignature
	}
}

// toolCalls parses the accumulated arguments. Malformed JSON is kept as a
// ParseError on the call so the agent loop can report it to the model.
func (a *toolCallAssembler) toolCalls() []ToolCall {
	if len(a.order) == 0 {
		return nil
	}
	out := make([]ToolCall, 0, len(a.order))
	for _, acc := range a.order {
		args := make(map[string]any)
		if err := json.Unmarshal([]byte(acc.rawArgs), &args); err != nil && acc.rawArgs != "" {
			slog.Warn("openai_stream: failed to parse tool call arguments",
				"tool", acc.Name, "raw_len", len(acc.rawArgs), "error", err)
			acc.ParseError = fmt.Sprintf("malformed JSON (%d chars): %v", len(acc.rawArgs), err)
		}
		acc.Arguments = args
		if acc.thoughtSig != "" {
			acc.Metadata = map[string]string{"thought_signature": acc.thoughtSig}
		}
		out = append(out, acc.ToolCall)
	}
	return out
}

// openAIStreamError is the in-band error object some upstreams send inside the
// SSE stream after the 200 header has already gone out.
type openAIStreamError struct {
	Message string `json:"message"`
	Type    string `json:"type,omitempty"`
	Code    any    `json:"code,omitempty"`
}

// httpError maps an in-band stream error to the HTTPError the retry and
// failover classifiers understand. A numeric code is used as the status when it
// is a valid HTTP status; anything else is treated as an upstream 502.
func (e *openAIStreamError) httpError(provider string) error {
	status := 502
	switch code := e.Code.(type) {
	case float64:
		if code >= 400 && code < 600 {
			status = int(code)
		}
	case string:
		if strings.Contains(strings.ToLower(code), "rate_limit") || strings.Contains(strings.ToLower(code), "cooldown") {
			status = 429
		}
	}
	body, _ := json.Marshal(map[string]any{"error": e})
	return fmt.Errorf("%s: stream error: %w", provider, &HTTPError{Status: status, Body: string(body)})
}
