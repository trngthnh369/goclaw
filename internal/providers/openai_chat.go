package providers

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
)

func (p *OpenAIProvider) Chat(ctx context.Context, req ChatRequest) (*ChatResponse, error) {
	if UsesStreamTransport(req) {
		return p.chatViaStream(ctx, req)
	}
	model := p.resolveModel(req.Model)
	body := p.buildRequestBody(model, req, false)
	body = ApplyMiddlewares(body, p.middlewares, p.middlewareConfig(model, req))

	chatFn := p.chatRequestFn(ctx, body)

	resp, err := RetryDo(ctx, p.retryConfig, chatFn)

	// Auto-clamp max_tokens and retry once if the model rejects the value
	if err != nil {
		if clamped := clampMaxTokensFromError(err, body); clamped {
			slog.Info("max_tokens clamped, retrying", "model", model, "limit", clampedLimit(body))
			resp, err = RetryDo(ctx, p.retryConfig, chatFn)
		}
	}

	// Drop user-visible reasoning for models flagged as leakers (e.g. Kimi,
	// DeepSeek-Reasoner). Usage.ThinkingTokens is preserved so billing stays
	// correct (Phase 1 depends on this).
	if resp != nil {
		if strip, _ := req.Options[OptStripThinking].(bool); strip {
			resp.Thinking = ""
		}
	}

	return resp, err
}

// chatRequestFn returns a closure that performs a single non-streaming chat request.
// Shared between initial attempt and post-clamp retry to avoid duplication.
func (p *OpenAIProvider) chatRequestFn(ctx context.Context, body map[string]any) func() (*ChatResponse, error) {
	return func() (*ChatResponse, error) {
		respBody, err := p.doRequest(ctx, body)
		if err != nil {
			return nil, err
		}
		defer respBody.Close()

		var oaiResp openAIResponse
		if err := json.NewDecoder(respBody).Decode(&oaiResp); err != nil {
			return nil, fmt.Errorf("%s: decode response: %w", p.name, err)
		}

		return p.parseResponse(&oaiResp), nil
	}
}

func (p *OpenAIProvider) ChatStream(ctx context.Context, req ChatRequest, onChunk func(StreamChunk)) (*ChatResponse, error) {
	model := p.resolveModel(req.Model)
	// stripThinking suppresses user-visible reasoning while leaving
	// Usage.ThinkingTokens untouched (the usage chunk below still records it).
	stripThinking, _ := req.Options[OptStripThinking].(bool)
	body := p.buildRequestBody(model, req, true)
	body = ApplyMiddlewares(body, p.middlewares, p.middlewareConfig(model, req))

	// Retry only the connection phase; once streaming starts, no retry.
	respBody, err := RetryDo(ctx, p.retryConfig, func() (io.ReadCloser, error) {
		return p.doRequest(ctx, body)
	})

	// Auto-clamp max_tokens and retry once if the model rejects the value
	if err != nil {
		if clamped := clampMaxTokensFromError(err, body); clamped {
			slog.Info("max_tokens clamped, retrying stream", "model", model, "limit", clampedLimit(body))
			respBody, err = RetryDo(ctx, p.retryConfig, func() (io.ReadCloser, error) {
				return p.doRequest(ctx, body)
			})
		}
	}
	if err != nil {
		return nil, err
	}
	// Wrap respBody so ctx cancellation closes the socket, unblocking bufio.Scanner.
	// The idle guard does the same for an upstream that stalls mid-stream.
	cb := NewCtxBody(ctx, newIdleTimeoutBody(respBody, StreamIdleTimeout))
	defer cb.Close()

	result := &ChatResponse{FinishReason: "stop"}
	var calls toolCallAssembler
	sawFinish := false

	sse := NewSSEScanner(cb)
	for sse.Next() {
		data := sse.Data()

		var chunk openAIStreamChunk
		if err := json.Unmarshal([]byte(data), &chunk); err != nil {
			continue
		}
		// An upstream failure after the 200 header arrives in-band. Surface it
		// as an HTTP error so retry and model fallback treat it like the
		// status code a non-stream request would have returned.
		if chunk.Error != nil {
			return nil, chunk.Error.httpError(p.name)
		}

		// Usage chunk often has empty choices — extract usage before skipping.
		// When stream_options.include_usage is true, the final chunk contains
		// usage data but choices is typically an empty array.
		if chunk.Usage != nil {
			result.Usage = &Usage{
				PromptTokens:     chunk.Usage.PromptTokens,
				CompletionTokens: chunk.Usage.CompletionTokens,
				TotalTokens:      chunk.Usage.TotalTokens,
				RequestCount:     1,
			}
			if chunk.Usage.PromptTokensDetails != nil {
				result.Usage.CacheReadTokens = chunk.Usage.PromptTokensDetails.CachedTokens
				result.Usage.CacheCreationTokens = chunk.Usage.PromptTokensDetails.CacheWriteTokens
				result.Usage.PromptTokensIncludeCachedSegments = true
			}
			if chunk.Usage.CompletionTokensDetails != nil && chunk.Usage.CompletionTokensDetails.ReasoningTokens > 0 {
				result.Usage.ThinkingTokens = chunk.Usage.CompletionTokensDetails.ReasoningTokens
			}
			if chunk.Usage.ServerToolUse != nil {
				result.Usage.WebSearchCount = chunk.Usage.ServerToolUse.WebSearchRequests
			}
		}

		if len(chunk.Choices) == 0 {
			continue
		}

		delta := chunk.Choices[0].Delta
		reasoning := delta.ReasoningContent
		if reasoning == "" {
			reasoning = delta.Reasoning
		}
		if reasoning != "" && !stripThinking {
			result.Thinking += reasoning
			if onChunk != nil {
				onChunk(StreamChunk{Thinking: reasoning})
			}
		}
		if delta.Content != "" {
			result.Content += delta.Content
			if onChunk != nil {
				onChunk(StreamChunk{Content: delta.Content})
			}
		}

		// Accumulate images from delta.images[].
		// Each chunk may carry one or more image parts; we collect all into result.Images.
		// Malformed data URLs are skipped with a warning — they don't abort the stream.
		for _, img := range delta.Images {
			mimeType, b64Data, err := parseDataURL(img.ImageURL.URL)
			if err != nil {
				slog.Warn("openai_stream: skipping malformed image data URL",
					"type", img.Type, "url_len", len(img.ImageURL.URL), "error", err)
				continue
			}
			result.Images = append(result.Images, ImageContent{
				MimeType: mimeType,
				Data:     b64Data,
			})
		}

		// Accumulate streamed tool calls
		for _, tc := range delta.ToolCalls {
			calls.add(tc)
		}

		if chunk.Choices[0].FinishReason != "" {
			result.FinishReason = chunk.Choices[0].FinishReason
			sawFinish = true
		}

	}

	// Check for scanner errors (timeout, connection reset, etc.)
	if err := sse.Err(); err != nil {
		return result, fmt.Errorf("%s: stream read error: %w", p.name, err)
	}

	result.ToolCalls = calls.toolCalls()

	// A stream that ends without a finish_reason was cut off upstream. When
	// nothing was delivered to a caller (stream transport for a non-streaming
	// channel), report it so retry or fallback runs, instead of returning an
	// empty or half-written answer as a success. The partial result is kept
	// for usage accounting.
	if onChunk == nil && !sawFinish {
		return result, fmt.Errorf("%s: stream ended without a response: %w", p.name,
			&HTTPError{Status: 502, Body: "upstream stream closed before finish_reason"})
	}

	// Only override finish_reason when stream wasn't truncated.
	// Preserve "length" so agent loop can detect truncation and retry.
	if len(result.ToolCalls) > 0 && result.FinishReason != "length" {
		result.FinishReason = "tool_calls"
	}

	if onChunk != nil {
		onChunk(StreamChunk{Done: true})
	}

	return result, nil
}

const maxToolCallIDLen = 40

// normalizeMistralToolCallID deterministically maps any tool call ID to a
// 9-character alphanumeric string required by the Mistral API.
// Uses SHA-256 of the full ID to avoid prefix-dependent collisions.
func normalizeMistralToolCallID(id string) string {
	h := sha256.Sum256([]byte(id))
	return hex.EncodeToString(h[:])[:9]
}

// wireToolCallID dispatches to Mistral-specific normalization (9-char alnum)
// or the standard OpenAI truncation (40-char max) based on the provider.
func (p *OpenAIProvider) wireToolCallID(id string) string {
	if p.name == "mistral" || p.providerType == "mistral" {
		return normalizeMistralToolCallID(id)
	}
	return truncateToolCallID(id)
}

// truncateToolCallID deterministically fits tool call IDs into OpenAI's 40-char
// limit. Prefix truncation can alias distinct legacy IDs that only diverge after
// byte 40, so we hash the full original ID when shortening is needed.
//
// Fresh tool calls from the agent loop already go through uniquifyToolCallIDs
// (which produces 40-char hashed IDs), so this is a no-op for those. This
// function catches replayed/legacy history entries that bypassed uniquification.
func truncateToolCallID(id string) string {
	if len(id) <= maxToolCallIDLen {
		return id
	}
	hash := sha256.Sum256([]byte(id))
	return "call_" + hex.EncodeToString(hash[:])[:maxToolCallIDLen-len("call_")]
}

// chatViaStream serves Chat over a streaming request (OptStreamTransport).
// Nothing reaches the caller until the answer is complete, so a mid-stream
// failure is retried as a whole like a non-stream request would be. The whole
// call is bounded by StreamTransportTimeout: the idle guard alone would let an
// upstream that keeps sending bytes hold the session lane indefinitely.
func (p *OpenAIProvider) chatViaStream(ctx context.Context, req ChatRequest) (*ChatResponse, error) {
	ctx, cancel := context.WithTimeout(ctx, StreamTransportTimeout)
	defer cancel()
	// ChatStream retries its own connection phase; only the outer loop retries.
	once := WithRetryMaxAttempts(ctx, 1)
	return RetryDo(ctx, p.retryConfig, func() (*ChatResponse, error) {
		return p.ChatStream(once, req, nil)
	})
}
