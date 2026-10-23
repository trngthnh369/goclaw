package tools

import "context"

// ToolAuthorizer decides whether a tool call may run.
//
// It is consulted inside Registry.ExecuteWithContext rather than in the agent
// pipeline because the pipeline is only one of several ways a tool reaches the
// registry: the MCP bridge, the HTTP tool-invoke endpoint and memory flush all
// call ExecuteWithContext directly. A gate in the pipeline protects none of
// them; a gate here covers every caller by construction.
type ToolAuthorizer interface {
	// AuthorizeTool returns a non-nil error to deny the call. The error text is
	// returned to the model, so it should say what to do, not leak internals.
	AuthorizeTool(ctx context.Context, req ToolAuthzRequest) error
}

// ToolAuthzRequest describes the call being authorized.
type ToolAuthzRequest struct {
	// Tool is the canonical name — aliases are already resolved, so a policy
	// can never be dodged by calling a tool under another name.
	Tool string

	// Meta is the tool's capability metadata, declared or inferred.
	Meta ToolMetadata

	// Args are the arguments as they will be passed to the tool. A policy that
	// binds an approval to specific arguments must hash these, not the ones it
	// saw earlier: a PreToolUse hook may have rewritten them in between.
	Args map[string]any

	// SystemReason is non-empty when the call was started by GoClaw itself with
	// no human in the loop (see WithSystemToolCall). The policy decides what to
	// do with that — the gate does not skip itself.
	SystemReason string
}

type systemToolCallKey struct{}

// WithSystemToolCall marks ctx as a system-initiated tool call and records why.
//
// Use it only for calls GoClaw originates on its own behalf, such as flushing
// memory at the end of a run. Never set it on a path that carries
// model-controlled input: it tells the policy that no human is available to
// approve, which a policy may treat as grounds to proceed.
func WithSystemToolCall(ctx context.Context, reason string) context.Context {
	if reason == "" {
		return ctx
	}
	return context.WithValue(ctx, systemToolCallKey{}, reason)
}

// SystemToolCallReason returns the reason set by WithSystemToolCall, if any.
func SystemToolCallReason(ctx context.Context) string {
	reason, _ := ctx.Value(systemToolCallKey{}).(string)
	return reason
}
