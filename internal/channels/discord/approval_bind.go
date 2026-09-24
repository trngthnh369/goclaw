package discord

import (
	"context"
	"fmt"
	"log/slog"
	"regexp"
	"strings"
	"time"

	"github.com/bwmarrin/discordgo"

	"github.com/nextlevelbuilder/goclaw/internal/bus"
	"github.com/nextlevelbuilder/goclaw/internal/tools"
)

// A publish approval used to require a Discord reply to the exact review
// draft. The two paths here remove that friction without weakening what is
// approved: both resolve one concrete bot-authored draft and then hand it to
// handleMessage as m.ReferencedMessage, so the existing evidence pipeline
// (same-channel check, attachment digests, approval allowlist, the message
// tool gate and its per-draft ledger) runs unchanged.
//
//   - Reaction: ✅ on a draft. The reaction names the draft, so there is
//     nothing to guess.
//
// A draft is recognised by the 📝 reaction the bot adds when it sends one
// (markIfReviewDraft), so drafts sent before that existed need a real reply.
//
//   - Bare command: "duyệt" typed without replying. Bound only when exactly
//     one draft is pending; otherwise the approver is asked to pick one.

const (
	pendingDraftScanLimit = 50
	pendingDraftMaxAge    = 72 * time.Hour
	approvalLookupTimeout = 10 * time.Second

	// publishedNoticePrefix starts the confirmation the Facebook channel sends
	// back after a post goes live (facebook/announce.go). Keep in sync.
	publishedNoticePrefix = "✅ Đã đăng lên fanpage."

	// reviewDraftMarker is the reaction the bot puts on its own review drafts
	// when it sends them. It is the only thing that makes a bot message a draft
	// for the approval shortcuts: text shape alone cannot tell an article from
	// a long conversational answer, and approving the wrong one publishes it.
	reviewDraftMarker = "📝"

	// approvalReactionCommand is what a reaction approval says on the
	// approver's behalf. It must itself pass tools.IsPositiveFeedPostApproval.
	approvalReactionCommand = "duyệt"
)

// 👍 is deliberately absent: it is the everyday "seen it" reaction, and an
// accidental one here would be an irreversible public post.
var approvalReactionEmoji = map[string]bool{"✅": true, "☑️": true, "✔️": true}

// publishedDraftPattern reads the draft id the publish confirmation carries.
var publishedDraftPattern = regexp.MustCompile(`(?m)^draft_id:\s*(\d+)\s*$`)

// handleReactionAdd turns an approval reaction on a review draft into the same
// inbound turn a "duyệt" reply would produce.
func (c *Channel) handleReactionAdd(_ *discordgo.Session, r *discordgo.MessageReactionAdd) {
	if r == nil || r.MessageReaction == nil || r.GuildID == "" {
		return
	}
	// Custom guild emoji carry an ID; only the unicode approval emoji count.
	if r.Emoji.ID != "" || !approvalReactionEmoji[r.Emoji.Name] {
		return
	}
	if r.UserID == "" || r.UserID == c.botUserID || !c.isExplicitApprovalSender(r.UserID) {
		return
	}

	ctx, cancel := context.WithTimeout(context.Background(), approvalLookupTimeout)
	defer cancel()
	draft, err := c.session.ChannelMessage(r.ChannelID, r.MessageID, discordgo.WithContext(ctx))
	if err != nil {
		slog.Warn("discord: approval reaction lookup failed", "message_id", r.MessageID, "error", err)
		return
	}
	if !c.isReviewDraft(draft, r.ChannelID, time.Now()) {
		return
	}
	// Reacting to a draft that is already live is common (a ✅ added after the
	// fact). Skip it here instead of starting a turn the ledger will refuse.
	if later, err := c.session.ChannelMessages(r.ChannelID, pendingDraftScanLimit, "", draft.ID, "", discordgo.WithContext(ctx)); err == nil {
		for _, msg := range later {
			if msg == nil || msg.Author == nil || msg.Author.ID != c.botUserID {
				continue
			}
			if match := publishedDraftPattern.FindStringSubmatch(msg.Content); match != nil && match[1] == draft.ID {
				return
			}
		}
	}

	var author *discordgo.User
	if r.Member != nil && r.Member.User != nil {
		author = r.Member.User
	} else if author, err = c.session.User(r.UserID, discordgo.WithContext(ctx)); err != nil {
		slog.Warn("discord: approval reaction user lookup failed", "user_id", r.UserID, "error", err)
		return
	}
	if author == nil || author.Bot || author.ID != r.UserID {
		return
	}

	slog.Warn("security.discord_reaction_approval",
		"channel_id", r.ChannelID, "draft_message_id", draft.ID, "approver_id", r.UserID)

	// The draft id doubles as the inbound message id: the gateway's inbound
	// dedup then drops a repeated reaction, and status reactions and replies
	// land on the draft being published.
	c.handleMessage(c.session, &discordgo.MessageCreate{Message: &discordgo.Message{
		ID:                draft.ID,
		ChannelID:         r.ChannelID,
		GuildID:           r.GuildID,
		Content:           approvalReactionCommand,
		Author:            author,
		Member:            r.Member,
		Timestamp:         time.Now(),
		ReferencedMessage: draft,
	}})
}

// resolveImplicitApproval binds a non-reply approval command to the single
// pending review draft. It returns the draft to bind, or handled=true when it
// already answered the approver and the message must not be processed.
func (c *Channel) resolveImplicitApproval(m *discordgo.MessageCreate) (draft *discordgo.Message, handled bool) {
	if m.ReferencedMessage != nil || m.GuildID == "" || !c.isExplicitApprovalSender(m.Author.ID) {
		return nil, false
	}
	if !tools.IsPositiveFeedPostApproval(c.stripBotMention(m.Content)) {
		return nil, false
	}

	ctx, cancel := context.WithTimeout(context.Background(), approvalLookupTimeout)
	defer cancel()
	history, err := c.session.ChannelMessages(m.ChannelID, pendingDraftScanLimit, m.ID, "", "", discordgo.WithContext(ctx))
	if err != nil {
		slog.Warn("discord: pending draft lookup failed", "channel_id", m.ChannelID, "error", err)
		return nil, false
	}

	pending := c.pendingReviewDrafts(history, m.ChannelID, time.Now())
	switch len(pending) {
	case 0:
		return nil, false
	case 1:
		slog.Warn("security.discord_implicit_approval_bind",
			"channel_id", m.ChannelID, "draft_message_id", pending[0].ID,
			"approver_id", m.Author.ID, "message_id", m.ID)
		return pending[0], false
	default:
		notice := fmt.Sprintf("Có %d bài nháp đang chờ duyệt nên mình chưa biết bạn duyệt bài nào. "+
			"Hãy reply \"duyệt\" vào đúng bài, hoặc thả ✅ lên bài đó.", len(pending))
		if _, err := c.session.ChannelMessageSend(m.ChannelID, notice); err != nil {
			slog.Warn("discord: ambiguous approval notice failed", "channel_id", m.ChannelID, "error", err)
		}
		return nil, true
	}
}

// pendingReviewDrafts returns the review drafts in history (newest first) that
// have not been published yet. A publish confirmation names its draft; an
// older confirmation without a draft id is treated as covering everything
// before it, which can only hide a draft, never approve a wrong one.
func (c *Channel) pendingReviewDrafts(history []*discordgo.Message, channelID string, now time.Time) []*discordgo.Message {
	published := map[string]bool{}
	var pending []*discordgo.Message
	for _, msg := range history {
		if msg == nil || msg.Author == nil || msg.Author.ID != c.botUserID {
			continue
		}
		if strings.HasPrefix(strings.TrimSpace(msg.Content), publishedNoticePrefix) {
			match := publishedDraftPattern.FindStringSubmatch(msg.Content)
			if match == nil {
				break
			}
			published[match[1]] = true
			continue
		}
		if published[msg.ID] || !c.isReviewDraft(msg, channelID, now) {
			continue
		}
		pending = append(pending, msg)
	}
	return pending
}

// isReviewDraft reports whether msg is a review draft: authored by this bot in
// the channel, marked 📝 by the bot itself when it was sent, recent, with text
// and at most one attachment - an image, or a video in a Reels review channel.
func (c *Channel) isReviewDraft(msg *discordgo.Message, channelID string, now time.Time) bool {
	if msg == nil || msg.Author == nil || msg.Author.ID != c.botUserID || msg.ChannelID != channelID {
		return false
	}
	if strings.TrimSpace(msg.Content) == "" || len(msg.Attachments) > 1 || !hasOwnMarker(msg) {
		return false
	}
	if len(msg.Attachments) == 1 {
		contentType := strings.ToLower(msg.Attachments[0].ContentType)
		isImage := strings.HasPrefix(contentType, "image/")
		isReelsVideo := strings.HasPrefix(contentType, "video/") && c.isReelsReviewChat(channelID)
		if !isImage && !isReelsVideo {
			return false
		}
	}
	return now.Sub(msg.Timestamp) <= pendingDraftMaxAge
}

func hasOwnMarker(msg *discordgo.Message) bool {
	for _, r := range msg.Reactions {
		if r != nil && r.Me && r.Emoji != nil && r.Emoji.ID == "" && r.Emoji.Name == reviewDraftMarker {
			return true
		}
	}
	return false
}

// markIfReviewDraft tags a just-sent ContentFactory review draft with the
// 📝 marker. Only a draft that went out as a single message is marked: the
// approval binds one message, so a split draft must be approved by reply.
func (c *Channel) markIfReviewDraft(msg bus.OutboundMessage, channelID string, ids []string) {
	if msg.Metadata[tools.MetaContentFactoryReviewDraft] != "true" {
		return
	}
	if len(ids) != 1 || ids[0] == "" {
		slog.Warn("discord: review draft not marked", "channel_id", channelID, "messages", len(ids))
		return
	}
	if err := c.session.MessageReactionAdd(channelID, ids[0], reviewDraftMarker); err != nil {
		slog.Warn("discord: review draft marker failed", "channel_id", channelID, "message_id", ids[0], "error", err)
	}
}

func (c *Channel) stripBotMention(content string) string {
	content = strings.ReplaceAll(content, "<@"+c.botUserID+">", "")
	content = strings.ReplaceAll(content, "<@!"+c.botUserID+">", "")
	return strings.TrimSpace(content)
}
