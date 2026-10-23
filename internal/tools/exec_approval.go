package tools

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/google/uuid"

	"github.com/nextlevelbuilder/goclaw/internal/store"
)

// ExecSecurity determines the overall security mode for command execution.
type ExecSecurity string

const (
	// ExecSecurityDeny blocks all commands (no exec tool available).
	ExecSecurityDeny ExecSecurity = "deny"

	// ExecSecurityAllowlist only allows commands matching the allowlist.
	ExecSecurityAllowlist ExecSecurity = "allowlist"

	// ExecSecurityFull allows all commands (ask mode still applies).
	ExecSecurityFull ExecSecurity = "full"
)

// ExecAskMode determines when to prompt for user approval.
type ExecAskMode string

const (
	// ExecAskOff never asks — commands are auto-approved.
	ExecAskOff ExecAskMode = "off"

	// ExecAskOnMiss asks only when a command is not in the allowlist.
	ExecAskOnMiss ExecAskMode = "on-miss"

	// ExecAskAlways asks for every command execution.
	ExecAskAlways ExecAskMode = "always"
)

// ExecApprovalConfig configures command execution approval.
type ExecApprovalConfig struct {
	Security  ExecSecurity `json:"security"`  // "deny", "allowlist", "full" (default "full")
	Ask       ExecAskMode  `json:"ask"`       // "off", "on-miss", "always" (default "off")
	Allowlist []string     `json:"allowlist"` // glob patterns for allowed commands
}

// DefaultExecApprovalConfig returns the default (permissive) config.
func DefaultExecApprovalConfig() ExecApprovalConfig {
	return ExecApprovalConfig{
		Security: ExecSecurityFull,
		Ask:      ExecAskOff,
	}
}

// safeBins are command names that are always considered safe.
// Only includes read-only, text processing, and dev tools.
// Infrastructure/network tools (docker, kubectl, terraform, ansible,
// curl, wget, ssh, scp, rsync) are excluded — they require approval
// when ask mode is "on-miss".
var safeBins = map[string]bool{
	// Read-only / info tools
	"cat": true, "echo": true, "ls": true, "pwd": true, "head": true,
	"tail": true, "wc": true, "sort": true, "uniq": true, "grep": true,
	"find": true, "which": true, "whoami": true, "date": true,
	"uname": true, "hostname": true,
	"df": true, "du": true, "free": true, "uptime": true, "file": true,
	"stat": true, "dirname": true, "basename": true, "realpath": true,
	// Text processing
	"jq": true, "yq": true, "sed": true, "awk": true, "tr": true,
	"cut": true, "diff": true, "patch": true, "tee": true, "xargs": true,
	// Dev tools (core purpose of a coding agent)
	"git": true, "node": true, "npm": true, "npx": true, "yarn": true,
	"pnpm": true, "bun": true, "deno": true, "python": true, "python3": true,
	"pip": true, "pip3": true, "go": true, "cargo": true, "rustc": true,
	"make": true, "cmake": true, "gcc": true, "g++": true, "clang": true,
	"java": true, "javac": true, "mvn": true, "gradle": true,
}

// ApprovalDecision is the user's response to an approval request.
type ApprovalDecision string

const (
	ApprovalAllowOnce ApprovalDecision = "allow-once"
	ApprovalDeny      ApprovalDecision = "deny"
)

// ErrApprovalUnavailable is returned when a request cannot be parked at all
// (shutting down, or too many approvals already waiting).
var ErrApprovalUnavailable = errors.New("approval unavailable")

// Limits on how many runs may sit parked on a human decision at once.
//
// A parked run holds a scheduler lane token for the whole timeout
// (internal/scheduler/lanes.go: the token is only returned after fn() returns),
// and the main lane is shared by every tenant. Without a cap, a handful of
// unanswered approvals starve unrelated tenants, so we deny past the cap
// instead of parking.
const (
	defaultMaxPendingGlobal   = 8
	defaultMaxPendingPerScope = 3
)

// PendingApproval is an in-flight approval request.
type PendingApproval struct {
	ID        string    `json:"id"`
	Command   string    `json:"command"`
	AgentID   string    `json:"agentId"`
	CreatedAt time.Time `json:"createdAt"`

	// Scope decides who may see and resolve this approval. It is derived from
	// the run context at request time, never from the tool struct: the exec
	// tool is a shared singleton whose agentID is the literal "default".
	TenantID   uuid.UUID `json:"-"`
	SessionKey string    `json:"-"`
	UserID     string    `json:"-"`
	// unscoped is set when no run context was available. Such a request cannot
	// be attributed to a tenant, so only master scope may see or resolve it.
	unscoped bool

	resultCh chan ApprovalDecision
	// resolved makes resolution single-shot: exactly one of
	// {Resolve, timeout, ctx cancel, shutdown drain} may win.
	resolved bool
}

// ExecApprovalManager manages pending approval requests.
type ExecApprovalManager struct {
	config  ExecApprovalConfig
	pending map[string]*PendingApproval
	mu      sync.Mutex

	maxPendingGlobal   int
	maxPendingPerScope int

	shuttingDown bool
}

// NewExecApprovalManager creates an approval manager with the given config.
func NewExecApprovalManager(cfg ExecApprovalConfig) *ExecApprovalManager {
	return &ExecApprovalManager{
		config:             cfg,
		pending:            make(map[string]*PendingApproval),
		maxPendingGlobal:   defaultMaxPendingGlobal,
		maxPendingPerScope: defaultMaxPendingPerScope,
	}
}

// approvalScope is the identity a pending approval is filed under.
type approvalScope struct {
	tenantID   uuid.UUID
	sessionKey string
	userID     string
	unscoped   bool
}

// scopeFromContext derives the approval scope from the run context.
//
// Fail-closed: with no run context there is nothing to attribute the request
// to, so it is marked unscoped and only master scope may act on it. Reading
// the tool struct instead would be worse than useless — SetApprovalManager
// passes the constant "default" as the agent id.
func scopeFromContext(ctx context.Context) approvalScope {
	if rc := store.RunContextFromCtx(ctx); rc != nil {
		return approvalScope{
			tenantID:   rc.TenantID,
			sessionKey: rc.SessionKey,
			userID:     rc.UserID,
		}
	}
	return approvalScope{unscoped: true}
}

// canAccess reports whether the caller's context may see or resolve pa.
func canAccess(ctx context.Context, pa *PendingApproval) bool {
	if store.IsMasterScope(ctx) {
		return true
	}
	if pa.unscoped {
		return false
	}
	return pa.TenantID == store.TenantIDFromContext(ctx)
}

// CheckCommand evaluates whether a command should be executed, blocked, or needs approval.
// Returns: "allow", "deny", or "ask".
func (m *ExecApprovalManager) CheckCommand(command string) string {
	switch m.config.Security {
	case ExecSecurityDeny:
		return "deny"

	case ExecSecurityAllowlist:
		if m.matchesAllowlist(command) {
			if m.config.Ask == ExecAskAlways {
				return "ask"
			}
			return "allow"
		}
		if m.config.Ask == ExecAskOff {
			return "deny" // not in allowlist, no asking
		}
		return "ask"

	case ExecSecurityFull:
		switch m.config.Ask {
		case ExecAskOff:
			return "allow"
		case ExecAskAlways:
			return "ask"
		case ExecAskOnMiss:
			if m.matchesAllowlist(command) || m.isSafeBin(command) {
				return "allow"
			}
			return "ask"
		}
	}

	return "allow"
}

// RequestApproval creates a pending approval and blocks until it is resolved,
// the run context is cancelled, the timeout expires, or the manager shuts down.
//
// The caller's ctx matters: without it an aborted run would leave a goroutine
// parked on a lane token for the full timeout, and graceful shutdown would
// block on wg.Wait() instead of denying.
func (m *ExecApprovalManager) RequestApproval(ctx context.Context, command, agentID string, timeout time.Duration) (ApprovalDecision, error) {
	scope := scopeFromContext(ctx)

	m.mu.Lock()
	if m.shuttingDown {
		m.mu.Unlock()
		return ApprovalDeny, fmt.Errorf("%w: gateway is shutting down", ErrApprovalUnavailable)
	}
	if len(m.pending) >= m.maxPendingGlobal {
		m.mu.Unlock()
		slog.Warn("security.approval_cap_reached", "scope", "global", "limit", m.maxPendingGlobal)
		return ApprovalDeny, fmt.Errorf("%w: too many approvals already awaiting a decision", ErrApprovalUnavailable)
	}
	inScope := 0
	for _, p := range m.pending {
		if p.unscoped == scope.unscoped && p.TenantID == scope.tenantID {
			inScope++
		}
	}
	if inScope >= m.maxPendingPerScope {
		m.mu.Unlock()
		slog.Warn("security.approval_cap_reached", "scope", "tenant",
			"tenant_id", scope.tenantID, "limit", m.maxPendingPerScope)
		return ApprovalDeny, fmt.Errorf("%w: too many approvals already awaiting a decision for this tenant", ErrApprovalUnavailable)
	}

	id := uuid.NewString()
	pa := &PendingApproval{
		ID:         id,
		Command:    command,
		AgentID:    agentID,
		CreatedAt:  time.Now(),
		TenantID:   scope.tenantID,
		SessionKey: scope.sessionKey,
		UserID:     scope.userID,
		unscoped:   scope.unscoped,
		resultCh:   make(chan ApprovalDecision, 1),
	}
	m.pending[id] = pa
	m.mu.Unlock()

	slog.Info("exec approval requested", "id", id, "command", truncateCmd(command, 100),
		"tenant_id", scope.tenantID, "session_key", scope.sessionKey, "unscoped", scope.unscoped)

	select {
	case decision := <-pa.resultCh:
		// Resolve (or the shutdown drain) already claimed it.
		return decision, nil

	case <-ctx.Done():
		if !m.claim(id) {
			return <-pa.resultCh, nil // lost the race; a real decision is already in flight
		}
		return ApprovalDeny, fmt.Errorf("approval cancelled: %w", ctx.Err())

	case <-time.After(timeout):
		if !m.claim(id) {
			return <-pa.resultCh, nil
		}
		return ApprovalDeny, fmt.Errorf("approval timed out after %s", timeout)
	}
}

// claim marks a pending approval resolved and removes it, returning true only
// for the single caller that won. Every terminal path goes through it so an
// operator can never be told "approved" for a call that actually timed out.
func (m *ExecApprovalManager) claim(id string) bool {
	m.mu.Lock()
	defer m.mu.Unlock()

	pa, ok := m.pending[id]
	if !ok || pa.resolved {
		return false
	}
	pa.resolved = true
	delete(m.pending, id)
	return true
}

// Resolve resolves a pending approval request on behalf of the caller in ctx.
func (m *ExecApprovalManager) Resolve(ctx context.Context, id string, decision ApprovalDecision) error {
	notFound := fmt.Errorf("approval %q not found or already resolved", id)

	m.mu.Lock()
	defer m.mu.Unlock()

	pa, ok := m.pending[id]
	if !ok || pa.resolved {
		return notFound
	}
	// Same error for "wrong tenant" as for "missing" — do not confirm existence
	// to a caller outside the approval's scope.
	if !canAccess(ctx, pa) {
		slog.Warn("security.approval_cross_tenant_denied", "id", id,
			"caller_tenant", store.TenantIDFromContext(ctx), "owner_tenant", pa.TenantID)
		return notFound
	}

	pa.resolved = true
	delete(m.pending, id)
	pa.resultCh <- decision // buffered, never blocks
	return nil
}

// ListPending returns the pending approvals visible to the caller in ctx:
// everything for master scope, otherwise the caller's own tenant.
func (m *ExecApprovalManager) ListPending(ctx context.Context) []*PendingApproval {
	m.mu.Lock()
	defer m.mu.Unlock()

	result := make([]*PendingApproval, 0, len(m.pending))
	for _, pa := range m.pending {
		if canAccess(ctx, pa) {
			result = append(result, pa)
		}
	}
	return result
}

// Shutdown stops accepting new approvals and denies everything still parked.
//
// Call this BEFORE cancelling runs and stopping lanes: a parked request holds a
// lane token, so draining last would let Lane.Stop's wg.Wait() block until every
// outstanding approval hit its full timeout.
func (m *ExecApprovalManager) Shutdown() {
	m.mu.Lock()
	defer m.mu.Unlock()

	m.shuttingDown = true
	for id, pa := range m.pending {
		if pa.resolved {
			continue
		}
		pa.resolved = true
		pa.resultCh <- ApprovalDeny
		delete(m.pending, id)
	}
	slog.Info("exec approval: drained pending approvals as denied")
}

// matchesAllowlist checks if a command matches any configured allowlist pattern.
//
// There is deliberately no dynamic "allow-always" store. The previous one was a
// process-global map keyed only by binary name, written by any operator's
// allow-always decision and read for every agent in every tenant, with no way to
// revoke it — one "always" on `pip` in one tenant allowlisted pip everywhere.
// It was also lost on restart, so nothing durable depended on it.
func (m *ExecApprovalManager) matchesAllowlist(command string) bool {
	bin := extractBin(command)

	// Check static allowlist patterns
	for _, pattern := range m.config.Allowlist {
		if matched, _ := filepath.Match(pattern, bin); matched {
			return true
		}
		// Also match against full command
		if matched, _ := filepath.Match(pattern, command); matched {
			return true
		}
	}

	return false
}

// isSafeBin checks if the command's base binary is in the safe list.
func (m *ExecApprovalManager) isSafeBin(command string) bool {
	return safeBins[extractBin(command)]
}

// extractBin returns the first word of a command (the binary name).
func extractBin(command string) string {
	command = strings.TrimSpace(command)
	// Skip env var assignments like FOO=bar cmd
	for strings.Contains(command, "=") {
		parts := strings.SplitN(command, " ", 2)
		if !strings.Contains(parts[0], "=") {
			break
		}
		if len(parts) < 2 {
			return ""
		}
		command = strings.TrimSpace(parts[1])
	}

	fields := strings.Fields(command)
	if len(fields) == 0 {
		return ""
	}
	return filepath.Base(fields[0])
}

func truncateCmd(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "..."
}
