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
	"testing"
	"time"
)

// slowAnswerServer flushes response headers at once (as a streaming upstream
// does) and only produces the answer after delay. For a non-stream request it
// writes nothing at all until the answer is ready, like a real upstream that
// generates before replying. It records whether each request asked to stream.
func slowAnswerServer(t *testing.T, delay time.Duration, streamed *[]bool) *httptest.Server {
	t.Helper()
	var mu sync.Mutex
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body map[string]any
		_ = json.NewDecoder(r.Body).Decode(&body)
		stream, _ := body["stream"].(bool)
		mu.Lock()
		*streamed = append(*streamed, stream)
		mu.Unlock()

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
	var streamed []bool
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
	if len(streamed) != 1 || !streamed[0] {
		t.Fatalf("requests streamed = %v, want one streaming request", streamed)
	}
}

func TestOpenAIChat_WithoutStreamTransportHitsHeaderTimeout(t *testing.T) {
	var streamed []bool
	p := newShortHeaderTimeoutProvider(slowAnswerServer(t, 300*time.Millisecond, &streamed))

	_, err := p.Chat(context.Background(), ChatRequest{
		Messages: []Message{{Role: "user", Content: "write a chapter"}},
	})
	if err == nil || !strings.Contains(err.Error(), "timeout awaiting response headers") {
		t.Fatalf("Chat() error = %v, want response header timeout", err)
	}
	if len(streamed) != 1 || streamed[0] {
		t.Fatalf("requests streamed = %v, want one non-stream request", streamed)
	}
}

func TestDashScopeChat_ToolsKeepNonStreamTransport(t *testing.T) {
	var streamed []bool
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
	if len(streamed) != 1 || streamed[0] {
		t.Fatalf("requests streamed = %v, want non-stream: DashScope rejects tools on a stream", streamed)
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
