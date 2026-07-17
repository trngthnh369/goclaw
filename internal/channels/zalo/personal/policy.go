package personal

import (
	"context"
	"fmt"
	"log/slog"
	"strings"
	"time"

	"github.com/nextlevelbuilder/goclaw/internal/channels"
	"github.com/nextlevelbuilder/goclaw/internal/channels/zalo/personal/protocol"
)

const pairingDebounce = 60 * time.Second

// checkDMPolicy enforces DM policy for incoming messages.
func (c *Channel) checkDMPolicy(ctx context.Context, senderID, chatID string) bool {
	result := c.CheckDMPolicy(ctx, senderID, c.config.DMPolicy)
	switch result {
	case channels.PolicyAllow:
		return true
	case channels.PolicyNeedsPairing:
		c.sendPairingReply(ctx, senderID, chatID)
		return false
	default:
		slog.Debug("zalo_personal DM rejected by policy", "sender_id", senderID, "policy", c.config.DMPolicy)
		return false
	}
}

// checkGroupPolicy enforces group access policy (allowlist/pairing).
// rc is the per-group resolved config (global → "*" → specific group).
// Returns false if the group is blocked by policy; does NOT check @mention gating.
func (c *Channel) checkGroupPolicy(ctx context.Context, senderID, groupID string, rc resolvedGroupConfig) bool {
	if !rc.enabled {
		slog.Debug("zalo_personal group message rejected: group disabled", "group_id", groupID)
		return false
	}

	// Per-group allowlist: the group having its own config entry implies the group
	// itself is allowed; senderID must match that group's allow_from (AND semantics).
	if rc.perGroupAllow && rc.groupPolicy == "allowlist" {
		if rc.senderInAllowFrom(senderID) {
			return true
		}
		slog.Debug("zalo_personal group message rejected by per-group allowlist",
			"group_id", groupID, "sender_id", senderID)
		return false
	}

	result := c.CheckGroupPolicy(ctx, senderID, groupID, rc.groupPolicy)
	switch result {
	case channels.PolicyAllow:
		return true
	case channels.PolicyNeedsPairing:
		groupSenderID := fmt.Sprintf("group:%s", groupID)
		c.sendPairingReply(ctx, groupSenderID, groupID)
		return false
	default:
		slog.Debug("zalo_personal group message rejected by policy", "group_id", groupID, "policy", rc.groupPolicy)
		return false
	}
}

func (c *Channel) sendPairingReply(ctx context.Context, senderID, chatID string) {
	ps := c.PairingService()
	sess := c.session()
	if ps == nil || sess == nil {
		return
	}

	if !c.CanSendPairingNotif(senderID, pairingDebounce) {
		return
	}

	code, err := ps.RequestPairing(ctx, senderID, c.Name(), chatID, "default", nil)
	if err != nil {
		slog.Debug("zalo_personal pairing request failed", "sender_id", senderID, "error", err)
		return
	}

	replyText := fmt.Sprintf(
		"GoClaw: access not configured.\n\nYour Zalo user id: %s\n\nPairing code: %s\n\nAsk the bot owner to approve with:\n  goclaw pairing approve %s",
		senderID, code, code,
	)

	threadType := protocol.ThreadTypeUser
	if strings.HasPrefix(senderID, "group:") {
		threadType = protocol.ThreadTypeGroup
	}

	sendCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if _, err := protocol.SendMessage(sendCtx, sess, chatID, threadType, replyText); err != nil {
		slog.Warn("zalo_personal: failed to send pairing reply", "error", err)
	} else {
		c.MarkPairingNotifSent(senderID)
		slog.Info("zalo_personal pairing reply sent", "sender_id", senderID, "code", code)
	}
}

// checkBotMentioned reports whether the bot is @mentioned in the message.
func (c *Channel) checkBotMentioned(mentions []*protocol.TMention) bool {
	sess := c.session()
	if sess == nil {
		return false
	}
	return isBotMentioned(sess.UID, mentions)
}

// isBotMentioned checks if the bot's UID is @mentioned in the message.
// Filters out @all mentions (Type=1, UID="-1") — only targeted @bot counts.
func isBotMentioned(botUID string, mentions []*protocol.TMention) bool {
	if botUID == "" {
		return false
	}

	for _, m := range mentions {
		if m == nil {
			continue
		}
		if m.Type == protocol.MentionAll || m.UID == protocol.MentionAllUID {
			continue
		}
		if m.UID == botUID {
			return true
		}
	}
	return false
}
