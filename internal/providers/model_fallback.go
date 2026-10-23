package providers

import (
	"context"
)

// FallbackCandidate is one runtime provider/model fallback option.
type FallbackCandidate struct {
	ProviderName string
	Model        string
	Provider     Provider
}

// ModelFallbackProvider wraps a primary provider with ordered fallback
// provider/model candidates. The primary candidate is always tried first.
type ModelFallbackProvider struct {
	primary     FallbackCandidate
	fallbacks   []FallbackCandidate
	classifier  ErrorClassifier
	tracker     *CooldownTracker
	maxAttempts int
}

type FallbackCallInfo struct {
	Streamed bool
	// StreamTransport is true when a Chat call ran over a stream without
	// delivering chunks (OptStreamTransport). Nothing reached the user, but the
	// upstream may have generated tokens before an error.
	StreamTransport bool
}

type FallbackAfterCall func(*ChatResponse, error, FallbackCallInfo)
type FallbackBeforeCall func(ctx context.Context, entry FallbackCandidate, req ChatRequest) (after FallbackAfterCall, err error)

func NewModelFallbackProvider(primary FallbackCandidate, fallbacks []FallbackCandidate, maxAttempts int, cooldownEnabled bool) *ModelFallbackProvider {
	var tracker *CooldownTracker
	if cooldownEnabled {
		tracker = NewCooldownTracker(0)
	}
	return &ModelFallbackProvider{
		primary:     primary,
		fallbacks:   fallbacks,
		classifier:  NewDefaultClassifier(),
		tracker:     tracker,
		maxAttempts: maxAttempts,
	}
}

func (p *ModelFallbackProvider) PrimaryProvider() Provider {
	return p.primary.Provider
}

func (p *ModelFallbackProvider) Name() string {
	if p.primary.Provider != nil {
		return p.primary.Provider.Name()
	}
	return p.primary.ProviderName
}

func (p *ModelFallbackProvider) DefaultModel() string {
	if p.primary.Model != "" {
		return p.primary.Model
	}
	if p.primary.Provider != nil {
		return p.primary.Provider.DefaultModel()
	}
	return ""
}

func (p *ModelFallbackProvider) Chat(ctx context.Context, req ChatRequest) (*ChatResponse, error) {
	return p.runOrdered(ctx, req, func(ctx context.Context, entry FallbackCandidate, req ChatRequest) (*ChatResponse, error) {
		nextReq := req
		nextReq.Model = entry.Model
		return entry.Provider.Chat(ctx, nextReq)
	})
}

func (p *ModelFallbackProvider) ChatWithHook(ctx context.Context, req ChatRequest, before FallbackBeforeCall) (*ChatResponse, error) {
	return p.runOrdered(ctx, req, func(ctx context.Context, entry FallbackCandidate, req ChatRequest) (*ChatResponse, error) {
		nextReq := req
		nextReq.Model = entry.Model
		after, err := before(ctx, entry, nextReq)
		if err != nil {
			return nil, err
		}
		resp, err := entry.Provider.Chat(ctx, nextReq)
		if after != nil {
			after(resp, err, FallbackCallInfo{StreamTransport: UsesStreamTransport(nextReq)})
		}
		return resp, err
	})
}

func (p *ModelFallbackProvider) ChatStream(ctx context.Context, req ChatRequest, onChunk func(StreamChunk)) (*ChatResponse, error) {
	return p.runOrdered(ctx, req, func(ctx context.Context, entry FallbackCandidate, req ChatRequest) (*ChatResponse, error) {
		nextReq := req
		nextReq.Model = entry.Model
		streamed := false
		resp, err := entry.Provider.ChatStream(ctx, nextReq, func(chunk StreamChunk) {
			if chunk.Content != "" || chunk.Thinking != "" || len(chunk.Images) > 0 {
				streamed = true
			}
			onChunk(chunk)
		})
		if streamed && err != nil {
			return nil, noFallbackAfterStreamError{err: err}
		}
		return resp, err
	})
}

func (p *ModelFallbackProvider) ChatStreamWithHook(ctx context.Context, req ChatRequest, onChunk func(StreamChunk), before FallbackBeforeCall) (*ChatResponse, error) {
	return p.runOrdered(ctx, req, func(ctx context.Context, entry FallbackCandidate, req ChatRequest) (*ChatResponse, error) {
		nextReq := req
		nextReq.Model = entry.Model
		after, err := before(ctx, entry, nextReq)
		if err != nil {
			return nil, err
		}
		streamed := false
		resp, err := entry.Provider.ChatStream(ctx, nextReq, func(chunk StreamChunk) {
			if chunk.Content != "" || chunk.Thinking != "" || len(chunk.Images) > 0 {
				streamed = true
			}
			onChunk(chunk)
		})
		if after != nil {
			after(resp, err, FallbackCallInfo{Streamed: streamed})
		}
		if streamed && err != nil {
			return nil, noFallbackAfterStreamError{err: err}
		}
		return resp, err
	})
}

func (p *ModelFallbackProvider) runOrdered(
	ctx context.Context,
	req ChatRequest,
	call func(context.Context, FallbackCandidate, ChatRequest) (*ChatResponse, error),
) (*ChatResponse, error) {
	candidates := p.orderedCandidates(req.Model)
	var attempts []FailoverAttempt
	for i, entry := range candidates {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		if p.maxAttempts > 0 && i >= p.maxAttempts {
			break
		}
		key := CooldownKey(entry.ProviderName, entry.Model)
		if p.tracker != nil && !p.tracker.IsAvailable(key) && !p.tracker.ShouldProbe(key) {
			continue
		}
		callCtx := ctx
		if p.hasNextCandidate(candidates, i) {
			callCtx = WithoutTimeoutRetry(ctx)
		}
		resp, err := call(callCtx, entry, req)
		if err == nil {
			if p.tracker != nil {
				p.tracker.RecordSuccess(key)
			}
			return resp, nil
		}
		if streamErr, ok := err.(noFallbackAfterStreamError); ok {
			return nil, streamErr.err
		}
		classification := ClassifyHTTPError(p.classifier, err)
		attempts = append(attempts, FailoverAttempt{
			Candidate:      ModelCandidate{Provider: entry.ProviderName, Model: entry.Model, ProfileID: entry.ProviderName + "/" + entry.Model},
			Classification: classification,
			Err:            err,
		})
		if p.tracker != nil && classification.Kind == "reason" {
			p.tracker.RecordFailure(key, classification.Reason)
		}
		if classification.Kind == "context_overflow" || classification.Reason == FailoverUnknown {
			return nil, err
		}
	}
	return nil, &FailoverSummaryError{Attempts: attempts}
}

// hasNextCandidate reports whether runOrdered may still try another candidate
// after index i: one within the attempt cap that is not cooling down. Probe
// eligibility is not consulted because ShouldProbe consumes the probe slot.
func (p *ModelFallbackProvider) hasNextCandidate(candidates []FallbackCandidate, i int) bool {
	for next := i + 1; next < len(candidates); next++ {
		if p.maxAttempts > 0 && next >= p.maxAttempts {
			return false
		}
		if p.tracker == nil || p.tracker.IsAvailable(CooldownKey(candidates[next].ProviderName, candidates[next].Model)) {
			return true
		}
	}
	return false
}

func (p *ModelFallbackProvider) orderedCandidates(requestModel string) []FallbackCandidate {
	primary := p.primary
	if requestModel != "" {
		primary.Model = requestModel
	}
	out := []FallbackCandidate{primary}
	for _, fallback := range p.fallbacks {
		if fallback.Provider == nil || fallback.ProviderName == "" || fallback.Model == "" {
			continue
		}
		if fallback.ProviderName == primary.ProviderName && fallback.Model == primary.Model {
			continue
		}
		out = append(out, fallback)
	}
	return out
}

type noFallbackAfterStreamError struct {
	err error
}

func (e noFallbackAfterStreamError) Error() string {
	return e.err.Error()
}
