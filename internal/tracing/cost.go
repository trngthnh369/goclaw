package tracing

import (
	"github.com/nextlevelbuilder/goclaw/internal/config"
	"github.com/nextlevelbuilder/goclaw/internal/providers"
)

// CalculateCost computes the USD cost for a single LLM call based on token usage and pricing.
// Returns 0 if pricing is nil.
//
// Semantics for reasoning/thinking tokens:
//
// All supported providers (OpenAI o3/o4-mini, Codex/GPT-5 Responses API, Anthropic
// extended thinking) report Usage.ThinkingTokens as a SUB-COUNT of Usage.CompletionTokens:
//   - OpenAI: completion_tokens includes reasoning; completion_tokens_details.reasoning_tokens is the breakdown.
//   - Codex: output_tokens includes reasoning; output_tokens_details.reasoning_tokens is the breakdown.
//   - Anthropic: output_tokens is total output; we estimate thinking tokens from streamed thinking_delta chars.
//
// Thus CompletionTokens already bills thinking at the output rate by default. Only
// when an explicit ReasoningPerMillion rate is configured do we split the two and
// price the thinking portion separately — otherwise we'd double-count.
func CalculateCost(pricing *config.ModelPricing, usage *providers.Usage) float64 {
	if pricing == nil || usage == nil {
		return 0
	}
	// OpenAI-style providers (openai, codex) report cached input inside PromptTokens;
	// Anthropic reports it separately. Price only the fresh share at the input rate.
	freshInput := usage.PromptTokens
	if usage.PromptTokensIncludeCachedSegments {
		freshInput = max(freshInput-usage.CacheReadTokens-usage.CacheCreationTokens, 0)
	}
	cost := float64(freshInput) * pricing.InputPerMillion / 1_000_000

	// Split completion tokens into visible output + thinking only when a distinct
	// ReasoningPerMillion rate is set. Otherwise price the full CompletionTokens
	// at OutputPerMillion — matches the provider billing semantics described above.
	if pricing.ReasoningPerMillion > 0 && usage.ThinkingTokens > 0 {
		visible := max(usage.CompletionTokens-usage.ThinkingTokens,
			// Defensive: thinkingChars/4 estimate for Anthropic may exceed OutputTokens
			// under unusual streaming conditions. Clamp to zero instead of going negative.
			0)
		cost += float64(visible) * pricing.OutputPerMillion / 1_000_000
		cost += float64(usage.ThinkingTokens) * pricing.ReasoningPerMillion / 1_000_000
	} else {
		cost += float64(usage.CompletionTokens) * pricing.OutputPerMillion / 1_000_000
	}

	cost += cacheSegmentCost(usage.CacheReadTokens, pricing.CacheReadPerMillion, pricing.InputPerMillion, usage.PromptTokensIncludeCachedSegments)
	cost += cacheSegmentCost(usage.CacheCreationTokens, pricing.CacheCreatePerMillion, pricing.InputPerMillion, usage.PromptTokensIncludeCachedSegments)
	return cost
}

// LookupPricing finds the model pricing from config.
// Tries "provider/model" first, then just "model".
func LookupPricing(pricingMap map[string]*config.ModelPricing, provider, model string) *config.ModelPricing {
	if pricingMap == nil {
		return nil
	}
	if p, ok := pricingMap[provider+"/"+model]; ok {
		return p
	}
	if p, ok := pricingMap[model]; ok {
		return p
	}
	return nil
}

// cacheSegmentCost prices one cache segment. A segment carved out of PromptTokens
// with no cache rate configured falls back to the input rate, so it is never free.
func cacheSegmentCost(tokens int, rate, inputRate float64, carvedFromPrompt bool) float64 {
	if tokens <= 0 {
		return 0
	}
	if rate <= 0 {
		if !carvedFromPrompt {
			return 0
		}
		rate = inputRate
	}
	return float64(tokens) * rate / 1_000_000
}
