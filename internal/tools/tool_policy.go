package tools

import (
	"context"
	"fmt"
	"log/slog"
	"time"
)

// Surface is where a run came from. It decides whether a human can be asked to
// approve something, which the agent alone cannot tell you: one agent serves
// interactive chat, cron, heartbeat and subagent runs through the same loop.
type Surface string

const (
	SurfaceUnknown      Surface = ""
	SurfaceChannelDM    Surface = "channel_dm"
	SurfaceChannelGroup Surface = "channel_group"
	SurfaceWS           Surface = "ws"
	SurfaceHTTP         Surface = "http"
	SurfaceCron         Surface = "cron"
	SurfaceHeartbeat    Surface = "heartbeat"
	SurfaceSubagent     Surface = "subagent"
)

// interactiveSurfaces are the surfaces where a person is attached and can
// answer an approval prompt.
//
// HTTP is deliberately absent: the OpenAI-compatible handlers call loop.Run
// directly and hold the request open, so parking there stalls an HTTP client
// for the whole timeout and then denies anyway.
var interactiveSurfaces = map[Surface]bool{
	SurfaceChannelDM:    true,
	SurfaceChannelGroup: true,
	SurfaceWS:           true,
}

var knownSurfaces = map[Surface]bool{
	SurfaceChannelDM:    true,
	SurfaceChannelGroup: true,
	SurfaceWS:           true,
	SurfaceHTTP:         true,
	SurfaceCron:         true,
	SurfaceHeartbeat:    true,
	SurfaceSubagent:     true,
}

// Known reports whether s is a surface this build understands. An unknown value
// is treated as untrusted rather than assumed safe.
func (s Surface) Known() bool { return knownSurfaces[s] }

// Interactive reports whether a human can be asked on this surface.
func (s Surface) Interactive() bool { return interactiveSurfaces[s] }

type surfaceKey struct{}

// WithSurface tags ctx with the surface that started the run.
//
// The surface must be derived by the server from how the run was created, never
// read from request input: a caller that can name its own surface can name an
// interactive one and choose who approves its work.
func WithSurface(ctx context.Context, s Surface) context.Context {
	return context.WithValue(ctx, surfaceKey{}, s)
}

// SurfaceFromContext returns the surface set by WithSurface, or SurfaceUnknown.
func SurfaceFromContext(ctx context.Context) Surface {
	s, _ := ctx.Value(surfaceKey{}).(Surface)
	return s
}

// PermissionMode is the per-agent approval policy.
type PermissionMode string

const (
	// PermissionModeDefault keeps the behaviour that predates the gate: the
	// registry does not ask, and exec still applies its own allowlist/ask rules.
	PermissionModeDefault PermissionMode = "default"

	// PermissionModeApproveMutating requires a human decision for any tool call
	// that is not purely read-only.
	PermissionModeApproveMutating PermissionMode = "approve-mutating"

	// PermissionModeBypass skips the human prompt and records that it did.
	PermissionModeBypass PermissionMode = "bypass"
)

// ParsePermissionMode converts stored text into a mode.
//
// An unrecognised value is an error rather than a silent fallback: a typo in a
// security setting must not quietly widen what an agent may do.
func ParsePermissionMode(s string) (PermissionMode, error) {
	switch PermissionMode(s) {
	case "":
		return PermissionModeDefault, nil
	case PermissionModeDefault:
		return PermissionModeDefault, nil
	case PermissionModeApproveMutating:
		return PermissionModeApproveMutating, nil
	case PermissionModeBypass:
		return PermissionModeBypass, nil
	default:
		return PermissionModeDefault, fmt.Errorf("unknown permission mode %q", s)
	}
}

// PermissionModeLookup returns the mode that applies to the run in ctx.
type PermissionModeLookup func(ctx context.Context) PermissionMode

// ApprovalRequester parks a call until a human decides, or gives up.
type ApprovalRequester interface {
	RequestApproval(ctx context.Context, command, agentID string, timeout time.Duration) (ApprovalDecision, error)
}

// ApprovalPolicy is the ToolAuthorizer that turns a permission mode plus a
// surface into an allow or deny for one tool call.
type ApprovalPolicy struct {
	mode      PermissionModeLookup
	approvals ApprovalRequester
	timeout   time.Duration
}

// NewApprovalPolicy builds the policy. A nil lookup means every run is in
// default mode.
func NewApprovalPolicy(mode PermissionModeLookup, approvals ApprovalRequester, timeout time.Duration) *ApprovalPolicy {
	if timeout <= 0 {
		timeout = 2 * time.Minute
	}
	return &ApprovalPolicy{mode: mode, approvals: approvals, timeout: timeout}
}

// AuthorizeTool implements ToolAuthorizer.
func (p *ApprovalPolicy) AuthorizeTool(ctx context.Context, req ToolAuthzRequest) error {
	mode := PermissionModeDefault
	if p.mode != nil {
		mode = p.mode(ctx)
	}

	switch mode {
	case PermissionModeDefault:
		return nil

	case PermissionModeBypass:
		slog.Warn("security.tool_approval_bypassed", "tool", req.Tool)
		return nil

	case PermissionModeApproveMutating:
		return p.requireApproval(ctx, req)

	default:
		// Unreachable through ParsePermissionMode, but a mode that reaches here
		// is one this build does not understand — refuse rather than guess.
		slog.Warn("security.tool_permission_mode_unknown", "mode", string(mode), "tool", req.Tool)
		return fmt.Errorf("%s is not allowed: the agent's permission mode is not recognised", req.Tool)
	}
}

func (p *ApprovalPolicy) requireApproval(ctx context.Context, req ToolAuthzRequest) error {
	// GoClaw's own bookkeeping (memory flush and the like) is not model-directed
	// and has nobody to ask; record it rather than stalling a background job.
	if req.SystemReason != "" {
		slog.Info("security.tool_approval_system_call", "tool", req.Tool, "reason", req.SystemReason)
		return nil
	}

	surface := SurfaceFromContext(ctx)
	if !surface.Known() {
		slog.Warn("security.tool_approval_unknown_surface", "tool", req.Tool, "surface", string(surface))
		return fmt.Errorf("%s is not allowed: this run has no recognised surface to approve from", req.Tool)
	}
	if !surface.Interactive() {
		// Deny immediately instead of parking. A parked run holds a scheduler
		// lane token shared with every tenant, and nobody is watching a cron or
		// heartbeat run, so parking here only converts a fast failure into a
		// slow one.
		slog.Info("security.tool_approval_no_human", "tool", req.Tool, "surface", string(surface))
		return fmt.Errorf("%s needs approval, and a %s run has nobody to ask", req.Tool, surface)
	}

	if p.approvals == nil {
		return fmt.Errorf("%s needs approval, but approvals are not configured", req.Tool)
	}

	// Only the tool name is shown. Arguments can carry secrets, file contents or
	// markup that imitates the prompt itself, so a richer preview needs its own
	// redaction contract before it reaches a chat channel.
	decision, err := p.approvals.RequestApproval(ctx, req.Tool, req.Tool, p.timeout)
	if err != nil {
		return fmt.Errorf("%s was not approved: %v", req.Tool, err)
	}
	if decision == ApprovalDeny {
		return fmt.Errorf("%s was denied", req.Tool)
	}
	return nil
}

// The real approval manager must satisfy the policy's requirement; if the two
// drift apart, this fails at compile time rather than at wiring time.
var _ ApprovalRequester = (*ExecApprovalManager)(nil)

// SurfaceForPeerKind maps a channel peer kind onto a surface. Both are
// interactive; they are kept apart because who may approve differs — a group
// has many members, a DM has one.
func SurfaceForPeerKind(peerKind string) Surface {
	if peerKind == "group" {
		return SurfaceChannelGroup
	}
	return SurfaceChannelDM
}
