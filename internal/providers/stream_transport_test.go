package providers

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

// slowAnswerServer flushes response headers at once (as a streaming upstream
// does) and only produces the answer after delay. For a non-stream request it
// writes nothing at all until the answer is ready, like a real upstream that
// generates before replying. It records whether each request asked to stream.
// streamLog records, per request, whether the client asked to stream. The
// handler can still be running after a client timeout, so access is locked.
type streamLog struct {
	mu   sync.Mutex
	seen []bool
}

func (l *streamLog) add(v bool) {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.seen = append(l.seen, v)
}

func (l *streamLog) get() []bool {
	l.mu.Lock()
	defer l.mu.Unlock()
	return append([]bool(nil), l.seen...)
}

func slowAnswerServer(t *testing.T, delay time.Duration, streamed *streamLog) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body map[string]any
		_ = json.NewDecoder(r.Body).Decode(&body)
		stream, _ := body["stream"].(bool)
		streamed.add(stream)

		if !stream {
			select {
			case <-r.Context().Done():
				return
			case <-time.After(delay):
			}
			w.Header().Set("Content-Type", "application/json")
			fmt.Fprint(w, `{"choices":[{"message":{"role":"assistant","content":"long answer"},"finish_reason":"stop"}]}`)
			return
		}
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		w.(http.Flusher).Flush()
		select {
		case <-r.Context().Done():
			return
		case <-time.After(delay):
		}
		fmt.Fprint(w, "data: {\"choices\":[{\"index\":0,\"delta\":{\"content\":\"long \"}}]}\n\n")
		fmt.Fprint(w, "data: {\"choices\":[{\"index\":0,\"delta\":{\"tool_calls\":[{\"index\":0,\"id\":\"call_1\",\"function\":{\"name\":\"datetime\",\"arguments\":\"{}\"}}]}}]}\n\n")
		fmt.Fprint(w, "data: {\"choices\":[{\"index\":0,\"delta\":{\"content\":\"answer\"},\"finish_reason\":\"tool_calls\"}],\"usage\":{\"prompt_tokens\":10,\"completion_tokens\":5,\"total_tokens\":15}}\n\n")
		fmt.Fprint(w, "data: [DONE]\n\n")
	}))
	t.Cleanup(srv.Close)
	return srv
}

func newShortHeaderTimeoutProvider(srv *httptest.Server) *OpenAIProvider {
	p := NewOpenAIProvider("upstream", "test-key", srv.URL, "slow-model")
	p.client = &http.Client{Transport: &http.Transport{ResponseHeaderTimeout: 100 * time.Millisecond}}
	p.retryConfig = RetryConfig{Attempts: 1}
	return p
}

func TestOpenAIChat_StreamTransportSurvivesLongGeneration(t *testing.T) {
	var streamed streamLog
	p := newShortHeaderTimeoutProvider(slowAnswerServer(t, 300*time.Millisecond, &streamed))

	resp, err := p.Chat(context.Background(), ChatRequest{
		Messages: []Message{{Role: "user", Content: "write a chapter"}},
		Options:  map[string]any{OptStreamTransport: true},
	})
	if err != nil {
		t.Fatalf("Chat() with stream transport error = %v", err)
	}
	if resp.Content != "long answer" {
		t.Fatalf("content = %q, want assembled %q", resp.Content, "long answer")
	}
	if len(resp.ToolCalls) != 1 || resp.ToolCalls[0].Name != "datetime" {
		t.Fatalf("tool calls = %+v, want one datetime call", resp.ToolCalls)
	}
	if resp.FinishReason != "tool_calls" {
		t.Fatalf("finish_reason = %q, want tool_calls", resp.FinishReason)
	}
	if resp.Usage == nil || resp.Usage.TotalTokens != 15 {
		t.Fatalf("usage = %+v, want total 15 from the stream", resp.Usage)
	}
	if got := streamed.get(); len(got) != 1 || !got[0] {
		t.Fatalf("requests streamed = %v, want one streaming request", got)
	}
}

func TestOpenAIChat_WithoutStreamTransportHitsHeaderTimeout(t *testing.T) {
	var streamed streamLog
	p := newShortHeaderTimeoutProvider(slowAnswerServer(t, 300*time.Millisecond, &streamed))

	_, err := p.Chat(context.Background(), ChatRequest{
		Messages: []Message{{Role: "user", Content: "write a chapter"}},
	})
	if err == nil || !strings.Contains(err.Error(), "timeout awaiting response headers") {
		t.Fatalf("Chat() error = %v, want response header timeout", err)
	}
	if got := streamed.get(); len(got) != 1 || got[0] {
		t.Fatalf("requests streamed = %v, want one non-stream request", got)
	}
}

func TestDashScopeChat_ToolsKeepNonStreamTransport(t *testing.T) {
	var streamed streamLog
	srv := slowAnswerServer(t, 0, &streamed)
	p := NewDashScopeProvider("dashscope", "test-key", srv.URL, "qwen-plus")

	_, err := p.Chat(context.Background(), ChatRequest{
		Messages: []Message{{Role: "user", Content: "hi"}},
		Tools:    []ToolDefinition{{Type: "function", Function: &ToolFunctionSchema{Name: "datetime"}}},
		Options:  map[string]any{OptStreamTransport: true},
	})
	if err != nil {
		t.Fatalf("Chat() error = %v", err)
	}
	if got := streamed.get(); len(got) != 1 || got[0] {
		t.Fatalf("requests streamed = %v, want non-stream: DashScope rejects tools on a stream", got)
	}
}

func TestIdleTimeoutBody_FailsStalledRead(t *testing.T) {
	pr, pw := io.Pipe()
	defer pw.Close()
	body := newIdleTimeoutBody(pr, 50*time.Millisecond)
	defer body.Close()

	go func() { _, _ = pw.Write([]byte("data: first\n\n")) }()
	buf := make([]byte, 64)
	if n, err := body.Read(buf); err != nil || n == 0 {
		t.Fatalf("first Read = %d, %v; want data", n, err)
	}

	start := time.Now()
	_, err := body.Read(buf) // upstream now stalls
	var netErr net.Error
	if !errors.As(err, &netErr) || !netErr.Timeout() {
		t.Fatalf("stalled Read error = %v, want a timeout net.Error", err)
	}
	if elapsed := time.Since(start); elapsed > 2*time.Second {
		t.Fatalf("stalled Read took %s, want about the idle limit", elapsed)
	}
}

func TestIdleTimeoutBody_ActiveStreamIsNotCut(t *testing.T) {
	pr, pw := io.Pipe()
	body := newIdleTimeoutBody(pr, 80*time.Millisecond)
	defer body.Close()

	go func() {
		for range 6 {
			time.Sleep(30 * time.Millisecond)
			_, _ = pw.Write([]byte("x"))
		}
		pw.Close()
	}()
	got, err := io.ReadAll(body)
	if err != nil {
		t.Fatalf("ReadAll error = %v; a stream with steady data must not time out", err)
	}
	if len(got) != 6 {
		t.Fatalf("read %d bytes, want 6", len(got))
	}
}

// sseServer replays fixed SSE data lines, then closes the stream.
func sseServer(t *testing.T, lines ...string) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		for _, l := range lines {
			fmt.Fprintf(w, "data: %s\n\n", l)
		}
	}))
	t.Cleanup(srv.Close)
	return srv
}

func streamTransportChat(t *testing.T, srv *httptest.Server) (*ChatResponse, error) {
	t.Helper()
	p := NewOpenAIProvider("upstream", "test-key", srv.URL, "m")
	p.retryConfig = RetryConfig{Attempts: 1}
	return p.Chat(context.Background(), ChatRequest{
		Messages: []Message{{Role: "user", Content: "hi"}},
		Options:  map[string]any{OptStreamTransport: true},
	})
}

func toolNames(calls []ToolCall) []string {
	out := make([]string, 0, len(calls))
	for _, c := range calls {
		out = append(out, c.Name)
	}
	return out
}

func TestOpenAIChatStream_ToolCallIndexesNotZeroBased(t *testing.T) {
	resp, err := streamTransportChat(t, sseServer(t,
		`{"choices":[{"index":0,"delta":{"tool_calls":[{"index":1,"id":"a","function":{"name":"first","arguments":"{}"}}]}}]}`,
		`{"choices":[{"index":0,"delta":{"tool_calls":[{"index":3,"id":"b","function":{"name":"second","arguments":"{\"x\":1}"}}]},"finish_reason":"tool_calls"}]}`,
		`[DONE]`,
	))
	if err != nil {
		t.Fatalf("Chat() error = %v", err)
	}
	if got := toolNames(resp.ToolCalls); len(got) != 2 || got[0] != "first" || got[1] != "second" {
		t.Fatalf("tool calls = %v, want [first second] in arrival order", got)
	}
}

func TestOpenAIChatStream_ParallelCallsSharingIndexZero(t *testing.T) {
	resp, err := streamTransportChat(t, sseServer(t,
		`{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"a","function":{"name":"datetime","arguments":"{}"}}]}}]}`,
		`{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"b","function":{"name":"web_search","arguments":"{\"q\":\"x\"}"}}]},"finish_reason":"tool_calls"}]}`,
		`[DONE]`,
	))
	if err != nil {
		t.Fatalf("Chat() error = %v", err)
	}
	if got := toolNames(resp.ToolCalls); len(got) != 2 || got[0] != "datetime" || got[1] != "web_search" {
		t.Fatalf("tool calls = %v, want two separate calls", got)
	}
	for _, c := range resp.ToolCalls {
		if c.ParseError != "" {
			t.Fatalf("call %s has parse error %q: arguments were glued together", c.Name, c.ParseError)
		}
	}
}

func TestOpenAIChatStream_ArgumentsSplitAcrossChunks(t *testing.T) {
	resp, err := streamTransportChat(t, sseServer(t,
		`{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"a","function":{"name":"web_search","arguments":"{\"q\":"}}]}}]}`,
		`{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\"goclaw\"}"}}]},"finish_reason":"tool_calls"}]}`,
		`[DONE]`,
	))
	if err != nil {
		t.Fatalf("Chat() error = %v", err)
	}
	if len(resp.ToolCalls) != 1 || resp.ToolCalls[0].Arguments["q"] != "goclaw" {
		t.Fatalf("tool calls = %+v, want one web_search with q=goclaw", resp.ToolCalls)
	}
}

func TestOpenAIChatStream_InBandErrorBecomesHTTPError(t *testing.T) {
	_, err := streamTransportChat(t, sseServer(t,
		`{"error":{"message":"All credentials for model are cooling down","code":"model_cooldown"}}`,
	))
	var httpErr *HTTPError
	if !errors.As(err, &httpErr) || httpErr.Status != 429 {
		t.Fatalf("error = %v, want an HTTPError 429 so failover moves on", err)
	}
}

func TestOpenAIChatStream_EmptyStreamFailsForTransport(t *testing.T) {
	_, err := streamTransportChat(t, sseServer(t, `[DONE]`))
	if err == nil || !strings.Contains(err.Error(), "without a response") {
		t.Fatalf("error = %v, want an empty-stream error so fallback can run", err)
	}
}

func TestOpenAIChatStream_EmptyAnswerWithFinishIsNotAnError(t *testing.T) {
	resp, err := streamTransportChat(t, sseServer(t,
		`{"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}`,
		`[DONE]`,
	))
	if err != nil || resp.Content != "" {
		t.Fatalf("Chat() = %+v, %v; a finished empty answer is a valid reply", resp, err)
	}
}

func TestOpenAIChatStream_TruncatedAnswerIsRetriedForTransport(t *testing.T) {
	var hits atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		if hits.Add(1) == 1 {
			// First attempt: half an answer, then the connection closes.
			fmt.Fprint(w, "data: {\"choices\":[{\"index\":0,\"delta\":{\"content\":\"half an\"}}]}\n\n")
			return
		}
		fmt.Fprint(w, "data: {\"choices\":[{\"index\":0,\"delta\":{\"content\":\"whole answer\"},\"finish_reason\":\"stop\"}]}\n\ndata: [DONE]\n\n")
	}))
	t.Cleanup(srv.Close)
	p := NewOpenAIProvider("upstream", "test-key", srv.URL, "m")
	p.retryConfig = RetryConfig{Attempts: 3, MinDelay: time.Millisecond, MaxDelay: 5 * time.Millisecond}

	resp, err := p.Chat(context.Background(), ChatRequest{
		Messages: []Message{{Role: "user", Content: "hi"}},
		Options:  map[string]any{OptStreamTransport: true},
	})
	if err != nil {
		t.Fatalf("Chat() error = %v", err)
	}
	if resp.Content != "whole answer" || hits.Load() != 2 {
		t.Fatalf("content = %q after %d requests, want the retried whole answer after 2", resp.Content, hits.Load())
	}
}

func TestIsRetryableError_StreamIdleIsNotRetried(t *testing.T) {
	err := fmt.Errorf("upstream: stream read error: %w", errStreamIdle{idle: time.Second})
	if IsRetryableError(err) {
		t.Fatal("an idle-timed-out stream must not be retried; failover handles it")
	}
}
