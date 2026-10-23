package personal

import (
	"github.com/nextlevelbuilder/goclaw/internal/config"
)

// resolvedGroupConfig holds the merged config for a specific Zalo group.
// Fields resolve in order: global ZaloPersonalConfig → wildcard group ("*") → specific group.
// Mirrors the Telegram resolveTopicConfig pattern (topic layer omitted — Zalo has no topics).
type resolvedGroupConfig struct {
	groupPolicy    string
	requireMention *bool
	allowFrom      []string // sender IDs allowed in this group; empty = inherit channel-level allow_from semantics
	enabled        bool
	// perGroupAllow is true when allowFrom came from a group-specific (or wildcard)
	// config block. In that case "allowlist" matches senderID against allowFrom only —
	// the group is already implicitly allowed by having a config entry.
	perGroupAllow bool
}

// resolveGroupConfig resolves the effective config for a Zalo group by merging layers.
func resolveGroupConfig(cfg config.ZaloPersonalConfig, groupID string) resolvedGroupConfig {
	result := resolvedGroupConfig{
		groupPolicy:    cfg.GroupPolicy,
		requireMention: cfg.RequireMention,
		allowFrom:      cfg.AllowFrom,
		enabled:        true,
	}

	if cfg.Groups == nil {
		return result
	}

	// Layer 1: wildcard ("*") — applies to all groups unless overridden.
	if wildcard, ok := cfg.Groups["*"]; ok && wildcard != nil {
		mergeGroupInto(&result, wildcard)
	}

	// Layer 2: specific group config — overrides wildcard.
	if groupCfg, ok := cfg.Groups[groupID]; ok && groupCfg != nil {
		mergeGroupInto(&result, groupCfg)
	}

	return result
}

// mergeGroupInto applies non-zero group config values over the current result.
func mergeGroupInto(dst *resolvedGroupConfig, src *config.ZaloGroupConfig) {
	if src.GroupPolicy != "" {
		dst.groupPolicy = src.GroupPolicy
	}
	if src.RequireMention != nil {
		dst.requireMention = src.RequireMention
	}
	if len(src.AllowFrom) > 0 {
		dst.allowFrom = src.AllowFrom
		dst.perGroupAllow = true
	}
	if src.Enabled != nil {
		dst.enabled = *src.Enabled
	}
}

// requireMentionOrDefault returns the resolved require_mention with the channel default (true).
func (r resolvedGroupConfig) requireMentionOrDefault() bool {
	if r.requireMention != nil {
		return *r.requireMention
	}
	return true
}

// senderInAllowFrom reports whether senderID matches the resolved per-group allow list.
func (r resolvedGroupConfig) senderInAllowFrom(senderID string) bool {
	for _, a := range r.allowFrom {
		if a == senderID {
			return true
		}
	}
	return false
}
