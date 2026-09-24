package store

import (
	"context"
	"strings"

	"github.com/google/uuid"

	"github.com/nextlevelbuilder/goclaw/internal/config"
	"github.com/nextlevelbuilder/goclaw/internal/sandbox"
)

// runContextKey is the context key for RunContext.
type runContextKey struct{}

// RunContext consolidates all agent-loop-injected context values into a single
// typed struct. This replaces 27 individual context.WithValue calls with one
// WithRunContext call, improving readability and making it trivial to add new
// scope fields (e.g. ProjectID).
//
// Consumers can read via RunContextFromCtx() or continue using existing
// accessor functions (which fall back to individual keys when RunContext is absent).
type RunContext struct {
	// Identity
	AgentID               uuid.UUID
	AgentKey              string
	TenantID              uuid.UUID
	UserID                string
	RunID                 string
	SessionKey            string
	CredentialUserID      string // resolved tenant user for credential lookups (empty = use UserID)
	AgentType             string
	SenderID              string
	InboundMessage        string // enriched message that triggered this run
	CurrentMessage        string // raw current user message without quoted/history context
	ReplyToMessageID      string // source channel message ID being replied to, when available
	ReplyToContent        string // source channel message content being replied to, when available
	ReplyToMedia          string // newline-separated filename=sha256 entries from the replied-to message
	ReplyToMediaPaths     []string
	ReplyToMediaCount     int    // number of attachments declared by the replied-to message
	ReplyToMediaComplete  bool   // true only when every declared attachment was downloaded and hashed
	ReplyToAuthorID       string // author ID of the replied-to message
	ChannelBotUserID      string // authenticated bot user ID for the source channel instance
	ApprovalSenderAllowed bool   // sender is explicitly allowlisted for public-post approval
	ApprovalPublishTarget string // publish target the channel instance binds to this chat ("reels"), empty when none

	// Flags
	SelfEvolve          bool
	SharedMemory        bool
	SharedKG            bool
	SharedSessions      bool
	SharedContext       bool
	RestrictToWorkspace bool

	// Tool configuration
	BuiltinToolSettings map[string][]byte
	Channel             string
	ChannelType         string
	ChannelContextScope ChannelContextScope
	SubagentsCfg        *config.SubagentsConfig
	ParentModel         string
	ParentProvider      string
	MemoryCfg           *config.MemoryConfig
	SandboxCfg          *sandbox.Config
	WaitToolCfg         *config.WaitToolPolicy
	ShellDenyGroups     map[string]bool

	// Workspace
	Workspace          string
	TeamWorkspace      string
	TeamID             string
	WorkspaceChannel   string
	WorkspaceChatID    string
	TeamIsolated       bool // true when team.workspace_scope != "shared" — drives chat_id filtering in vault search
	TeamTaskID         string
	DelegationID       string   // delegation identifier for vault auto-linking (empty when not in delegation)
	LeaderAgentID      string   // leader's agent UUID for member memory read fallback
	AgentToolKey       string   // tool-level agent key for registry routing
	TenantAllowedPaths []string // tenant-specific allowed paths beyond workspace (from system_configs)
}

// WithRunContext stores a RunContext on the context.
func WithRunContext(ctx context.Context, rc *RunContext) context.Context {
	return context.WithValue(ctx, runContextKey{}, rc)
}

// RunContextFromCtx extracts RunContext from context. Returns nil if not set.
// IsAllowlistedReplyToBot reports whether the turn that started this run is an
// explicitly allowlisted person replying to a message this channel's bot wrote.
// Public-post approval checks the same facts, one by one, for its error text.
func (rc *RunContext) IsAllowlistedReplyToBot() bool {
	return rc != nil && rc.ApprovalSenderAllowed &&
		strings.TrimSpace(rc.SenderID) != "" &&
		strings.TrimSpace(rc.ReplyToMessageID) != "" &&
		strings.TrimSpace(rc.ChannelBotUserID) != "" &&
		rc.ReplyToAuthorID == rc.ChannelBotUserID
}

func RunContextFromCtx(ctx context.Context) *RunContext {
	rc, _ := ctx.Value(runContextKey{}).(*RunContext)
	return rc
}
