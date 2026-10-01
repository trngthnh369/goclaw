package pipeline

import (
	"context"
	"strings"
	"testing"

	"github.com/nextlevelbuilder/goclaw/internal/providers"
)

// captureCacheKey runs one think iteration and returns the prompt cache key
// the stage put on the ChatRequest (empty when absent).
func captureCacheKey(t *testing.T, state *RunState) string {
	t.Helper()
	var got string
	deps := &PipelineDeps{
		Config: PipelineConfig{MaxIterations: 10, MaxTokens: 1000},
		CallLLM: func(_ context.Context, _ *RunState, req providers.ChatRequest) (*providers.ChatResponse, error) {
			got, _ = req.Options[providers.OptPromptCacheKey].(string)
			return &providers.ChatResponse{Content: "ok", FinishReason: "stop"}, nil
		},
	}
	if err := NewThinkStage(deps).Execute(context.Background(), state); err != nil {
		t.Fatalf("Execute() error: %v", err)
	}
	return got
}

func TestThinkStage_SessionKeySet_PromptCacheKeyStableAndHashed(t *testing.T) {
	t.Parallel()
	sessionKey := "agent:codex:codex-discord:group:1552179009540857876"
	input := minimalInput()
	input.SessionKey = sessionKey

	first := captureCacheKey(t, stateWithInput(input))
	second := captureCacheKey(t, stateWithInput(input))

	if first == "" {
		t.Fatal("prompt cache key missing")
	}
	if first != second {
		t.Errorf("key not stable across runs: %q vs %q", first, second)
	}
	if strings.Contains(first, "1552179009540857876") || strings.Contains(first, "codex") {
		t.Errorf("key leaks the raw session key: %q", first)
	}

	other := minimalInput()
	other.SessionKey = "agent:codex:http-896694335670726676-198be705"
	if captureCacheKey(t, stateWithInput(other)) == first {
		t.Error("different sessions share one cache key")
	}
}

func TestThinkStage_EmptySessionKey_PromptCacheKeyOmitted(t *testing.T) {
	t.Parallel()
	input := minimalInput()
	input.SessionKey = ""

	if got := captureCacheKey(t, stateWithInput(input)); got != "" {
		t.Errorf("key = %q, want none for an empty session key", got)
	}
}

func TestThinkStage_PromptCacheKeyPerSender(t *testing.T) {
	t.Parallel()
	keyFor := func(sender string) string {
		input := minimalInput()
		input.SessionKey = "agent:codex:codex-discord:group:1552179009540857876"
		input.SenderID = sender
		return captureCacheKey(t, stateWithInput(input))
	}

	turti, other, none := keyFor("896694335670726676"), keyFor("111"), keyFor("")
	if turti == other || turti == none || other == none {
		t.Errorf("senders share a cache key: turti=%q other=%q none=%q", turti, other, none)
	}
	if renamed := keyFor("896694335670726676|Turti"); renamed != turti {
		t.Errorf("display-name suffix split the cache: %q vs %q", renamed, turti)
	}
	if strings.Contains(turti, "896694335670726676") {
		t.Errorf("key leaks the raw sender ID: %q", turti)
	}
}
