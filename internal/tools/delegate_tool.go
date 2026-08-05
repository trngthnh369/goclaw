package tools

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/google/uuid"

	"github.com/nextlevelbuilder/goclaw/internal/bus"
	"github.com/nextlevelbuilder/goclaw/internal/eventbus"
	"github.com/nextlevelbuilder/goclaw/internal/hooks"
	"github.com/nextlevelbuilder/goclaw/internal/store"
)

// DelegateResult carries the delegatee's response content and any media produced.
type DelegateResult struct {
	Content string
	Media   []bus.MediaFile
}

// DelegateRunFunc dispatches a delegation to a target agent.
// Injected by the gateway to avoid circular dependency with agent package.
// Returns the delegatee's response content + media, or error.
type DelegateRunFunc func(ctx context.Context, req DelegateRequest) (DelegateResult, error)

// DelegateRequest describes a delegation dispatch.
type DelegateRequest struct {
	FromAgentID  uuid.UUID
	FromAgentKey string
	ToAgentKey   string
	Task         string
	DelegationID string
	UserID       string
	SenderID     string // real acting sender preserved through delegate announce re-ingress (#915)
	Role         string // caller's RBAC role; bypasses per-user grants for admin/operator/owner (#915)
	TenantID     string
	Channel      string
	ChatID       string
	PeerKind     string
	SessionKey   string
}

const contentFactoryDesignerDelegationActionKey = "contentfactory-designer-delegation"

// ContentFactory deployment constants. These are temporary — the audit gate is
// bound to a mutable agent_key, so renaming the agent disables it and another
// tenant reusing the name inherits it. Tracked for a move into team settings
// keyed by team UUID.
const (
	contentFactoryDirectorAgentKey = "cf-director"
	contentFactoryDesignerAgentKey = "cf-designer"
)

// ContentFactoryGatedAssignee reports whether an agent key may only be reached
// through the audited delegate path. The team_tasks auto-dispatch path performs
// no audit check, so it must refuse these targets outright rather than try to
// reproduce the gate (before managed runs exist there is no per-batch audit row
// for a dispatcher to read).
func ContentFactoryGatedAssignee(agentKey string) bool {
	return agentKey == contentFactoryDesignerAgentKey
}

// DelegateTool implements the `delegate` tool for inter-agent task delegation.
// Uses existing agent_links infrastructure for permission checks.
type DelegateTool struct {
	links          store.AgentLinkStore
	agents         store.AgentCRUDStore
	eventBus       eventbus.DomainEventBus
	runFn          DelegateRunFunc
	msgBus         *bus.MessageBus  // for async announce back to parent
	hookDispatcher hooks.Dispatcher // optional; nil-safe
}

// SetMsgBus sets the message bus for async result delivery to parent agent.
func (t *DelegateTool) SetMsgBus(mb *bus.MessageBus) { t.msgBus = mb }

// SetHookDispatcher sets the hook dispatcher for SubagentStart/Stop events.
func (t *DelegateTool) SetHookDispatcher(d hooks.Dispatcher) { t.hookDispatcher = d }

// NewDelegateTool creates a delegate tool.
func NewDelegateTool(links store.AgentLinkStore, agents store.AgentCRUDStore, eb eventbus.DomainEventBus, runFn DelegateRunFunc) *DelegateTool {
	return &DelegateTool{links: links, agents: agents, eventBus: eb, runFn: runFn}
}

func (t *DelegateTool) Name() string { return "delegate" }

func (t *DelegateTool) Description() string {
	return "Delegate a task to a linked agent. The target agent must be connected via an agent link."
}

func (t *DelegateTool) Parameters() map[string]any {
	return map[string]any{
		"type": "object",
		"properties": map[string]any{
			"agent_key": map[string]any{
				"type":        "string",
				"description": "The agent_key of the target agent to delegate to",
			},
			"task": map[string]any{
				"type":        "string",
				"description": "Description of the task to delegate",
			},
			"mode": map[string]any{
				"type":        "string",
				"enum":        []string{"async", "sync"},
				"description": "async: fire-and-forget (default), sync: wait for completion",
			},
			"timeout": map[string]any{
				"type":        "integer",
				"description": "Timeout in seconds for sync mode (default: 300)",
			},
		},
		"required": []string{"agent_key", "task"},
	}
}

func (t *DelegateTool) Execute(ctx context.Context, args map[string]any) *Result {
	agentKey, _ := args["agent_key"].(string)
	task, _ := args["task"].(string)
	mode, _ := args["mode"].(string)
	if mode == "" {
		mode = "async"
	}
	timeoutSec := 300
	if ts, ok := args["timeout"].(float64); ok && int(ts) > 0 {
		timeoutSec = int(ts)
	}
	if timeoutSec > 600 {
		timeoutSec = 600 // hard cap to prevent resource exhaustion
	}

	if agentKey == "" || task == "" {
		return ErrorResult("agent_key and task are required")
	}

	// Resolve calling agent from context
	fromAgentID := store.AgentIDFromContext(ctx)
	if fromAgentID == uuid.Nil {
		return ErrorResult("delegate requires agent context")
	}

	// Resolve target agent
	target, err := t.agents.GetByKey(ctx, agentKey)
	if err != nil {
		return ErrorResult(fmt.Sprintf("target agent %q not found", agentKey))
	}

	// Permission check via agent_links
	allowed, err := t.links.CanDelegate(ctx, fromAgentID, target.ID)
	if err != nil {
		slog.Warn("delegate.permission_check_error", "from", fromAgentID, "to", target.ID, "error", err)
		return ErrorResult("failed to check delegation permission")
	}
	if !allowed {
		return ErrorResult(fmt.Sprintf("no delegation link from current agent to %q", agentKey))
	}

	fromAgentKey := store.AgentKeyFromContext(ctx)
	if fromAgentKey == "" {
		fromAgentKey = ToolAgentKeyFromCtx(ctx)
	}
	// The audit gate keys on the caller identity. An unresolvable caller must
	// not silently skip it — for a gated target, no identity means no delegation.
	if agentKey == contentFactoryDesignerAgentKey && fromAgentKey == "" {
		slog.Warn("security.delegate.gate_caller_unresolved", "to", agentKey)
		return ErrorResult("cannot delegate to cf-designer without a resolved caller identity")
	}
	isContentFactoryDesignerDelegation := fromAgentKey == contentFactoryDirectorAgentKey && agentKey == contentFactoryDesignerAgentKey
	if isContentFactoryDesignerDelegation {
		if !hasAnchoredLine(task, "AUDIT_VERDICT: PASS") || !hasAnchoredLine(task, "SAFE_TO_SEND_DISCORD: yes") {
			return ErrorResult("cf-designer delegation requires line-anchored AUDIT_VERDICT: PASS and SAFE_TO_SEND_DISCORD: yes")
		}
		if mode != "sync" {
			return ErrorResult("cf-designer delegation must use sync mode")
		}
		latch := OutboundActionLatchFromCtx(ctx)
		if latch == nil {
			return ErrorResult("cf-designer delegation requires a run-scoped latch")
		}
		if !latch.TryReserve(contentFactoryDesignerDelegationActionKey) {
			result, _ := json.Marshal(map[string]any{
				"agent":  agentKey,
				"status": "duplicate_suppressed",
			})
			return NewResult(string(result))
		}
	}

	delegationID := uuid.New().String()
	// Audit-trail identity = actor (real sender). Groups audit actions to the
	// individual user rather than the group principal (#915).
	actorID := store.ActorIDFromContext(ctx)

	req := DelegateRequest{
		FromAgentID:  fromAgentID,
		FromAgentKey: store.AgentKeyFromContext(ctx),
		ToAgentKey:   agentKey,
		Task:         task,
		DelegationID: delegationID,
		UserID:       actorID,
		SenderID:     store.SenderIDFromContext(ctx),
		Role:         store.RoleFromContext(ctx),
		TenantID:     store.TenantIDFromContext(ctx).String(),
		Channel:      ToolChannelFromCtx(ctx),
		ChatID:       ToolChatIDFromCtx(ctx),
		PeerKind:     ToolPeerKindFromCtx(ctx),
		SessionKey:   ToolSessionKeyFromCtx(ctx),
	}

	// Emit delegate.sent event
	t.emitEvent(ctx, eventbus.EventDelegateSent, eventbus.DelegateSentPayload{
		DelegationID: delegationID,
		FromAgent:    req.FromAgentKey,
		ToAgent:      agentKey,
		Task:         task,
		Mode:         mode,
	})

	// Fire SubagentStart hook (blocking). Nil-safe: skip if no dispatcher.
	if t.hookDispatcher != nil {
		evt := hooks.Event{
			EventID:   uuid.NewString(),
			SessionID: req.SessionKey,
			TenantID:  parseUUIDOrNil(req.TenantID),
			AgentID:   req.FromAgentID,
			HookEvent: hooks.EventSubagentStart,
			Depth:     hooks.DepthFrom(ctx),
		}
		r, err := t.hookDispatcher.Fire(ctx, evt)
		if err != nil {
			t.emitEvent(ctx, eventbus.EventDelegateFailed, eventbus.DelegateFailedPayload{
				DelegationID: req.DelegationID,
				FromAgent:    req.FromAgentKey,
				ToAgent:      req.ToAgentKey,
				Error:        fmt.Sprintf("subagent_start hook error: %v", err),
			})
			return ErrorResult(fmt.Sprintf("subagent_start hook error: %v", err))
		}
		// Updated* from FireResult intentionally unused — delegate has no
		// mutation need in Wave 1.
		if r.Decision == hooks.DecisionBlock {
			t.emitEvent(ctx, eventbus.EventDelegateFailed, eventbus.DelegateFailedPayload{
				DelegationID: req.DelegationID,
				FromAgent:    req.FromAgentKey,
				ToAgent:      req.ToAgentKey,
				Error:        "blocked by subagent_start hook",
			})
			return ErrorResult(fmt.Sprintf("delegation to %q blocked by hook policy", req.ToAgentKey))
		}
		// Increment depth so nested delegate calls honor MaxLoopDepth.
		ctx = hooks.IncDepth(ctx)
	}

	if mode == "sync" {
		return t.executeSyncMode(ctx, req, timeoutSec, isContentFactoryDesignerDelegation)
	}
	return t.executeAsyncMode(ctx, req)
}

// executeSyncMode blocks until the delegatee completes or timeout.
func (t *DelegateTool) executeSyncMode(ctx context.Context, req DelegateRequest, timeoutSec int, requireExactlyOneImage bool) *Result {
	syncCtx, cancel := context.WithTimeout(ctx, time.Duration(timeoutSec)*time.Second)
	defer cancel()

	dr, err := t.runFn(syncCtx, req)
	if err != nil {
		t.emitEvent(ctx, eventbus.EventDelegateFailed, eventbus.DelegateFailedPayload{
			DelegationID: req.DelegationID,
			FromAgent:    req.FromAgentKey,
			ToAgent:      req.ToAgentKey,
			Error:        err.Error(),
		})
		return ErrorResult(fmt.Sprintf("delegation to %q failed: %v", req.ToAgentKey, err))
	}

	t.emitEvent(ctx, eventbus.EventDelegateCompleted, eventbus.DelegateCompletedPayload{
		DelegationID: req.DelegationID,
		FromAgent:    req.FromAgentKey,
		ToAgent:      req.ToAgentKey,
		Content:      truncate(dr.Content, 500),
		MediaCount:   len(dr.Media),
	})

	media := t.stageSyncDelegateMedia(ctx, req.DelegationID, dr.Media)
	if requireExactlyOneImage {
		imageCount := 0
		for _, m := range media {
			if strings.HasPrefix(m.MimeType, "image/") {
				imageCount++
			}
		}
		if imageCount != 1 {
			return ErrorResult(fmt.Sprintf("ContentFactory designer must produce exactly 1 staged image, got %d", imageCount))
		}
	}
	mediaJSON := make([]map[string]string, 0, len(media))
	for _, m := range media {
		mediaJSON = append(mediaJSON, map[string]string{
			"path":      m.Path,
			"mime_type": m.MimeType,
			"filename":  m.Filename,
			"media_ref": "MEDIA:" + m.Path,
		})
	}

	resultJSON, _ := json.Marshal(map[string]any{
		"delegation_id": req.DelegationID,
		"agent":         req.ToAgentKey,
		"status":        "completed",
		"content":       dr.Content,
		"media":         mediaJSON,
	})
	r := NewResult(string(resultJSON))
	r.Media = media
	return r
}

func (t *DelegateTool) stageSyncDelegateMedia(ctx context.Context, delegationID string, media []bus.MediaFile) []bus.MediaFile {
	if len(media) == 0 {
		return nil
	}
	workspace := ToolWorkspaceFromCtx(ctx)
	if workspace == "" {
		return media
	}
	stageDir := filepath.Join(workspace, "delegations", delegationID)
	if err := os.MkdirAll(stageDir, 0o755); err != nil {
		slog.Warn("delegate.media_stage_dir_failed", "delegation_id", delegationID, "error", err)
		return media
	}

	staged := make([]bus.MediaFile, 0, len(media))
	for i, m := range media {
		if m.Path == "" {
			continue
		}
		src := filepath.Clean(m.Path)
		if realSrc, err := filepath.EvalSymlinks(src); err == nil {
			src = realSrc
		}
		if wsReal, err := filepath.EvalSymlinks(workspace); err == nil && isPathInside(src, wsReal) {
			staged = append(staged, m)
			continue
		}

		name := m.Filename
		if name == "" {
			name = filepath.Base(src)
		}
		if name == "." || name == string(filepath.Separator) {
			name = fmt.Sprintf("delegate-media-%d", i+1)
		}
		dst := filepath.Join(stageDir, filepath.Base(name))
		if err := copyDelegateMediaFile(src, dst); err != nil {
			slog.Warn("delegate.media_stage_failed", "delegation_id", delegationID, "src", src, "error", err)
			continue
		}
		staged = append(staged, bus.MediaFile{Path: dst, MimeType: m.MimeType, Filename: filepath.Base(dst), Caption: m.Caption})
	}
	if len(staged) == 0 {
		return media
	}
	return staged
}

func copyDelegateMediaFile(src, dst string) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	out, err := os.OpenFile(dst, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o644)
	if err != nil {
		return err
	}
	if _, err := io.Copy(out, in); err != nil {
		_ = out.Close()
		return err
	}
	return out.Close()
}

// executeAsyncMode spawns a goroutine and returns immediately.
func (t *DelegateTool) executeAsyncMode(ctx context.Context, req DelegateRequest) *Result {
	// Detach from parent cancel but add a deadline to prevent goroutine leaks.
	bgCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), 10*time.Minute)

	go func() {
		defer cancel()
		dr, err := t.runFn(bgCtx, req)
		if err != nil {
			t.emitEvent(bgCtx, eventbus.EventDelegateFailed, eventbus.DelegateFailedPayload{
				DelegationID: req.DelegationID,
				FromAgent:    req.FromAgentKey,
				ToAgent:      req.ToAgentKey,
				Error:        err.Error(),
			})
			slog.Warn("delegate.async.failed", "to", req.ToAgentKey, "error", err)
			t.announceToParent(req, fmt.Sprintf("[Delegation to %s failed: %v]", req.ToAgentKey, err), nil)
			return
		}
		t.emitEvent(bgCtx, eventbus.EventDelegateCompleted, eventbus.DelegateCompletedPayload{
			DelegationID: req.DelegationID,
			FromAgent:    req.FromAgentKey,
			ToAgent:      req.ToAgentKey,
			Content:      truncate(dr.Content, 500),
			MediaCount:   len(dr.Media),
		})
		t.announceToParent(req, fmt.Sprintf("[Delegation result from %s]\n\n%s", req.ToAgentKey, dr.Content), dr.Media)
	}()

	result, _ := json.Marshal(map[string]any{
		"delegation_id": req.DelegationID,
		"agent":         req.ToAgentKey,
		"status":        "delegated",
		"message":       fmt.Sprintf("Task delegated to %s. You will be notified when complete.", req.ToAgentKey),
	})
	return NewResult(string(result))
}

// announceToParent delivers the delegate result back to the parent agent's
// conversation via msgBus, following the same pattern as subagent announce.
func (t *DelegateTool) announceToParent(req DelegateRequest, content string, media []bus.MediaFile) {
	if t.msgBus == nil || req.ChatID == "" {
		return
	}
	tenantUUID, _ := uuid.Parse(req.TenantID)
	meta := map[string]string{
		"origin_channel":     req.Channel,
		"origin_peer_kind":   req.PeerKind,
		"origin_session_key": req.SessionKey,
		"delegation_id":      req.DelegationID,
		"delegate_from":      req.FromAgentKey,
		"delegate_to":        req.ToAgentKey,
		MetaParentAgent:      req.FromAgentKey,
	}
	if req.SenderID != "" {
		meta[MetaOriginSenderID] = req.SenderID
	}
	if req.Role != "" {
		meta[MetaOriginRole] = req.Role
	}
	if req.UserID != "" {
		meta[MetaOriginUserID] = req.UserID
	}
	t.msgBus.PublishInbound(bus.InboundMessage{
		Channel:  "system",
		SenderID: fmt.Sprintf("subagent:delegate:%s", req.DelegationID),
		ChatID:   req.ChatID,
		Content:  content,
		Media:    media,
		UserID:   req.UserID,
		TenantID: tenantUUID,
		Metadata: meta,
	})
}

// parseUUIDOrNil parses s as a UUID; returns uuid.Nil on failure.
func parseUUIDOrNil(s string) uuid.UUID {
	id, err := uuid.Parse(s)
	if err != nil {
		return uuid.Nil
	}
	return id
}

func (t *DelegateTool) emitEvent(ctx context.Context, eventType eventbus.EventType, payload any) {
	if t.eventBus == nil {
		return
	}
	t.eventBus.Publish(eventbus.DomainEvent{
		ID:        uuid.New().String(),
		Type:      eventType,
		TenantID:  store.TenantIDFromContext(ctx).String(),
		AgentID:   store.AgentIDFromContext(ctx).String(),
		UserID:    store.ActorIDFromContext(ctx), // audit actor, not scope (#915)
		Timestamp: time.Now().UTC(),
		Payload:   payload,
	})
}

// hasAnchoredLine checks whether marker appears as a complete line (possibly
// with leading whitespace) in text. Prevents substring spoofing like
// "AUDIT_VERDICT: PASSIVE" matching "AUDIT_VERDICT: PASS".
func hasAnchoredLine(text, marker string) bool {
	for _, line := range strings.Split(text, "\n") {
		if strings.TrimSpace(line) == marker {
			return true
		}
	}
	return false
}
