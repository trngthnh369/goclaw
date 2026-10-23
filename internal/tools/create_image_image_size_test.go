package tools

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"

	"github.com/nextlevelbuilder/goclaw/internal/providers"
)

// chatImageProvider is an API-key provider named "openrouter", so create_image
// routes it to the chat-completions image endpoint (callImageGenAPI).
type chatImageProvider struct{ base string }

func (p *chatImageProvider) Name() string         { return "openrouter" }
func (p *chatImageProvider) DefaultModel() string { return "test-image-model" }
func (p *chatImageProvider) APIKey() string       { return "test-key" }
func (p *chatImageProvider) APIBase() string      { return p.base }
func (p *chatImageProvider) Chat(context.Context, providers.ChatRequest) (*providers.ChatResponse, error) {
	return &providers.ChatResponse{}, nil
}
func (p *chatImageProvider) ChatStream(context.Context, providers.ChatRequest, func(providers.StreamChunk)) (*providers.ChatResponse, error) {
	return &providers.ChatResponse{}, nil
}

type imageSizeHarness struct {
	tool   *CreateImageTool
	ctx    context.Context
	hits   *atomic.Int32
	bodies chan map[string]any
}

// newImageSizeHarness wires create_image to a fake chat image endpoint; chainParams
// is the provider chain entry's params JSON ("" for none).
func newImageSizeHarness(t *testing.T, chainParams string) imageSizeHarness {
	t.Helper()
	h := imageSizeHarness{hits: &atomic.Int32{}, bodies: make(chan map[string]any, 4)}
	dataURL := "data:image/png;base64," + base64.StdEncoding.EncodeToString(testPNGBytes())
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		h.hits.Add(1)
		var body map[string]any
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Errorf("decode request body: %v", err)
		}
		h.bodies <- body
		fmt.Fprintf(w, `{"choices":[{"message":{"images":[{"image_url":{"url":%q}}]}}]}`, dataURL)
	}))
	t.Cleanup(srv.Close)

	registry := providers.NewRegistry(nil)
	registry.Register(&chatImageProvider{base: srv.URL})
	params := ""
	if chainParams != "" {
		params = `,"params":` + chainParams
	}
	chainJSON := []byte(`{"providers":[{"provider":"openrouter","model":"test-image-model","enabled":true,"timeout":30,"max_retries":1` + params + `}]}`)
	h.ctx = WithToolWorkspace(WithBuiltinToolSettings(t.Context(), BuiltinToolSettings{"create_image": chainJSON}), t.TempDir())
	h.tool = NewCreateImageTool(registry)
	return h
}

func (h imageSizeHarness) run(t *testing.T, args map[string]any) (map[string]any, *Result) {
	t.Helper()
	res := h.tool.Execute(h.ctx, args)
	if res.IsError {
		return nil, res
	}
	select {
	case body := <-h.bodies:
		return body, res
	default:
		t.Fatal("no request reached the image endpoint")
		return nil, res
	}
}

func imageConfigOf(body map[string]any) map[string]any {
	cfg, _ := body["image_config"].(map[string]any)
	return cfg
}

func TestCreateImageImageSizeForwardedWithAspectRatio(t *testing.T) {
	h := newImageSizeHarness(t, "")
	body, res := h.run(t, map[string]any{"prompt": "a lantern street", "aspect_ratio": "9:16", "image_size": "2K"})
	if res.IsError {
		t.Fatalf("unexpected error: %s", res.ForLLM)
	}
	cfg := imageConfigOf(body)
	if cfg["aspect_ratio"] != "9:16" || cfg["image_size"] != "2K" {
		t.Fatalf("image_config = %v, want aspect_ratio 9:16 and image_size 2K", cfg)
	}
}

func TestCreateImageImageSizeAbsentKeepsBodyUnchanged(t *testing.T) {
	h := newImageSizeHarness(t, "")
	body, res := h.run(t, map[string]any{"prompt": "a lantern street"})
	if res.IsError {
		t.Fatalf("unexpected error: %s", res.ForLLM)
	}
	if _, present := body["image_config"]; present {
		t.Fatalf("image_config present without aspect ratio or size: %v", body["image_config"])
	}
}

func TestCreateImageImageSizeAloneOnSquare(t *testing.T) {
	h := newImageSizeHarness(t, "")
	body, _ := h.run(t, map[string]any{"prompt": "a lantern street", "image_size": "1K"})
	cfg := imageConfigOf(body)
	if len(cfg) != 1 || cfg["image_size"] != "1K" {
		t.Fatalf("image_config = %v, want only image_size 1K", cfg)
	}
}

func TestCreateImageImageSizeInvalidRefusedBeforeHTTP(t *testing.T) {
	for _, bad := range []string{"3K", "4K", "large"} {
		t.Run(bad, func(t *testing.T) {
			h := newImageSizeHarness(t, "")
			res := h.tool.Execute(h.ctx, map[string]any{"prompt": "x", "image_size": bad})
			if !res.IsError {
				t.Fatalf("image_size %q accepted", bad)
			}
			if n := h.hits.Load(); n != 0 {
				t.Fatalf("image endpoint hit %d times for an invalid size", n)
			}
		})
	}
}

func TestCreateImageImageSizeNormalizesCase(t *testing.T) {
	h := newImageSizeHarness(t, "")
	body, res := h.run(t, map[string]any{"prompt": "x", "image_size": " 2k "})
	if res.IsError {
		t.Fatalf("unexpected error: %s", res.ForLLM)
	}
	if got := imageConfigOf(body)["image_size"]; got != "2K" {
		t.Fatalf("image_size = %v, want 2K", got)
	}
}

func TestCreateImageImageSizeChainDefault(t *testing.T) {
	cases := []struct {
		name, chain, arg string
		want             any // nil = no image_size key
	}{
		{"chain 2K forwarded", `{"image_size":"2K"}`, "", "2K"},
		{"chain auto ignored", `{"image_size":"auto"}`, "", nil},
		{"chain invalid dropped", `{"image_size":"4K"}`, "", nil},
		{"arg overrides chain", `{"image_size":"2K"}`, "1K", "1K"},
		{"no params entry", "", "", nil},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			h := newImageSizeHarness(t, tc.chain)
			args := map[string]any{"prompt": "x", "aspect_ratio": "9:16"}
			if tc.arg != "" {
				args["image_size"] = tc.arg
			}
			body, res := h.run(t, args)
			if res.IsError {
				t.Fatalf("unexpected error: %s", res.ForLLM)
			}
			got, present := imageConfigOf(body)["image_size"]
			if tc.want == nil {
				if present {
					t.Fatalf("image_size = %v, want absent", got)
				}
				return
			}
			if got != tc.want {
				t.Fatalf("image_size = %v, want %v", got, tc.want)
			}
		})
	}
}
