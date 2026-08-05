package tools

import (
	"context"
	"errors"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/google/uuid"

	"github.com/nextlevelbuilder/goclaw/internal/providers"
	"github.com/nextlevelbuilder/goclaw/internal/providers/providertest"
	"github.com/nextlevelbuilder/goclaw/internal/store"
)

type countingImageProvider struct {
	name      string
	calls     int
	data      []byte
	returnErr error
}

func (p *countingImageProvider) Name() string {
	if p.name != "" {
		return p.name
	}
	return "contentfactory-image-test"
}
func (p *countingImageProvider) DefaultModel() string { return "test-image-model" }
func (p *countingImageProvider) Chat(context.Context, providers.ChatRequest) (*providers.ChatResponse, error) {
	return &providers.ChatResponse{}, nil
}
func (p *countingImageProvider) ChatStream(context.Context, providers.ChatRequest, func(providers.StreamChunk)) (*providers.ChatResponse, error) {
	return &providers.ChatResponse{}, nil
}
func (p *countingImageProvider) GenerateImage(_ context.Context, _ providers.NativeImageRequest) (*providers.NativeImageResult, error) {
	p.calls++
	if p.returnErr != nil {
		return nil, p.returnErr
	}
	return &providers.NativeImageResult{MimeType: "image/png", Data: p.data}, nil
}

func testPNGBytes() []byte {
	return []byte{
		0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a,
		0x00, 0x00, 0x00, 0x00,
		0x49, 0x45, 0x4e, 0x44,
		0xae, 0x42, 0x60, 0x82,
	}
}

func contentFactoryImageTestTool(t *testing.T) (*CreateImageTool, *countingImageProvider, context.Context) {
	t.Helper()

	provider := &countingImageProvider{data: testPNGBytes()}
	registry := providers.NewRegistry(nil)
	registry.Register(provider)

	chainJSON := []byte(`{"providers":[{"provider":"contentfactory-image-test","model":"test-image-model","enabled":true,"timeout":30,"max_retries":1}]}`)
	ctx := WithBuiltinToolSettings(t.Context(), BuiltinToolSettings{"create_image": chainJSON})
	ctx = WithToolWorkspace(ctx, t.TempDir())
	ctx = WithOutboundActionLatch(ctx, NewOutboundActionLatch())

	return NewCreateImageTool(registry), provider, ctx
}

func TestCreateImageContentFactoryDesignerAllowsExactlyOneGeneration(t *testing.T) {
	tool, provider, ctx := contentFactoryImageTestTool(t)
	ctx = WithToolAgentKey(ctx, "cf-designer")

	first := tool.Execute(ctx, map[string]any{"prompt": "safe editorial illustration"})
	if first.IsError {
		t.Fatalf("first create_image returned error: %s", first.ForLLM)
	}
	if provider.calls != 1 {
		t.Fatalf("provider calls after first generation = %d, want 1", provider.calls)
	}
	for _, marker := range []string{
		"DESIGN_STATUS: COMPLETE",
		"IMAGE_COUNT: 1",
		"IMAGE_PATH: MEDIA:",
		"Do not call create_image, list_files, or any other tool again",
	} {
		if !strings.Contains(first.ForLLM, marker) {
			t.Errorf("first result missing %q: %s", marker, first.ForLLM)
		}
	}

	second := tool.Execute(ctx, map[string]any{"prompt": "should not reach provider"})
	if !second.IsError {
		t.Fatalf("second create_image should fail closed, got: %s", second.ForLLM)
	}
	if provider.calls != 1 {
		t.Fatalf("provider calls after duplicate = %d, want 1", provider.calls)
	}
	if !strings.Contains(second.ForLLM, "already attempted") {
		t.Fatalf("duplicate result missing terminal guidance: %s", second.ForLLM)
	}
}

func TestCreateImageContentFactoryDesignerRequiresLatch(t *testing.T) {
	tool, provider, ctx := contentFactoryImageTestTool(t)
	ctx = WithToolAgentKey(context.Background(), "cf-designer")
	ctx = WithBuiltinToolSettings(ctx, BuiltinToolSettingsFromCtx(ctx))

	result := tool.Execute(ctx, map[string]any{"prompt": "should not reach provider"})
	if !result.IsError {
		t.Fatalf("cf-designer without latch should fail closed, got: %s", result.ForLLM)
	}
	if provider.calls != 0 {
		t.Fatalf("provider calls = %d, want 0 when latch is missing", provider.calls)
	}
	if !strings.Contains(result.ForLLM, "run-scoped latch") {
		t.Fatalf("missing latch error should explain latch requirement: %s", result.ForLLM)
	}
}

func TestCreateImageContentFactoryDesignerDoesNotFallbackOrRetry(t *testing.T) {
	first := &countingImageProvider{name: "contentfactory-image-test", data: testPNGBytes(), returnErr: errors.New("primary unavailable")}
	fallback := &countingImageProvider{name: "contentfactory-image-fallback", data: testPNGBytes()}
	registry := providers.NewRegistry(nil)
	registry.Register(first)
	registry.Register(fallback)

	chainJSON := []byte(`{"providers":[{"provider":"contentfactory-image-test","model":"test-image-model","enabled":true,"timeout":30,"max_retries":3},{"provider":"contentfactory-image-fallback","model":"test-image-model","enabled":true,"timeout":30,"max_retries":1}]}`)
	ctx := WithBuiltinToolSettings(t.Context(), BuiltinToolSettings{"create_image": chainJSON})
	ctx = WithToolWorkspace(ctx, t.TempDir())
	ctx = WithToolAgentKey(ctx, "cf-designer")
	ctx = WithOutboundActionLatch(ctx, NewOutboundActionLatch())

	result := NewCreateImageTool(registry).Execute(ctx, map[string]any{"prompt": "single allowed attempt"})
	if !result.IsError {
		t.Fatalf("expected primary failure to surface for cf-designer, got: %s", result.ForLLM)
	}
	if first.calls != 1 {
		t.Fatalf("primary calls = %d, want 1", first.calls)
	}
	if fallback.calls != 0 {
		t.Fatalf("fallback calls = %d, want 0", fallback.calls)
	}
}

func TestCreateImageContentFactoryDesignerBypassesProviderPoolFailover(t *testing.T) {
	tenantID := uuid.New()
	var primaryHits, fallbackHits atomic.Int32
	primaryServer := pool429Server(t, &primaryHits)
	fallbackServer := poolSSEServer(t, &fallbackHits)

	primary := providertest.NewCodexProviderFast("cf-pool-primary", primaryServer.URL)
	fallback := providertest.NewCodexProviderFast("cf-pool-fallback", fallbackServer.URL)
	primary.WithRoutingDefaults("round_robin", []string{"cf-pool-fallback"})
	registry := buildPoolChainRegistry(tenantID, primary, fallback)

	chainJSON := []byte(`{"providers":[{"provider":"cf-pool-primary","model":"gpt-image-2","enabled":true,"timeout":30,"max_retries":1}]}`)
	ctx := store.WithTenantID(t.Context(), tenantID)
	ctx = WithBuiltinToolSettings(ctx, BuiltinToolSettings{"create_image": chainJSON})
	ctx = WithToolWorkspace(ctx, t.TempDir())
	ctx = WithToolAgentKey(ctx, "cf-designer")
	ctx = WithOutboundActionLatch(ctx, NewOutboundActionLatch())

	result := NewCreateImageTool(registry).Execute(ctx, map[string]any{"prompt": "single upstream request"})
	if !result.IsError {
		t.Fatalf("expected primary 429 to surface without pool failover, got: %s", result.ForLLM)
	}
	if primaryHits.Load() != 1 {
		t.Fatalf("primary requests = %d, want 1", primaryHits.Load())
	}
	if fallbackHits.Load() != 0 {
		t.Fatalf("pool fallback requests = %d, want 0", fallbackHits.Load())
	}
}

func TestCreateImageOtherAgentsRemainUnrestricted(t *testing.T) {
	tool, provider, ctx := contentFactoryImageTestTool(t)
	ctx = WithToolAgentKey(ctx, "general-designer")

	for range 2 {
		result := tool.Execute(ctx, map[string]any{"prompt": "generate another image"})
		if result.IsError {
			t.Fatalf("non-ContentFactory create_image returned error: %s", result.ForLLM)
		}
	}
	if provider.calls != 2 {
		t.Fatalf("provider calls = %d, want 2 for non-ContentFactory agent", provider.calls)
	}
}
