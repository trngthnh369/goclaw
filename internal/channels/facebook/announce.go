package facebook

import (
	"context"
	"fmt"
	"log/slog"
	"strings"

	"github.com/nextlevelbuilder/goclaw/internal/bus"
)

// announcePublished tells whoever approved the post that it is live, and where.
//
// The publisher has to send this itself. The post id is created here and cannot
// travel back to the message tool — OutboundDispatcher returns only an error —
// and the ContentFactory director is instructed to answer NO_REPLY as soon as
// its terminal tool returns, so nothing downstream is in a position to report
// the outcome. Before this, approving a draft produced no confirmation at all
// and the operator had to grep container logs to find the post id.
//
// Best-effort by design: a publish that succeeded must never be reported as
// failed because the notice could not be delivered.
// afterPublish runs the two things that must happen once a feed post lands:
// record it so its performance can be read later, and tell the approver.
//
// The permalink is fetched once here and shared. It must be fetched, never
// assembled: Graph addresses page posts under the page's own numeric id, which
// differs from the page_id used to publish, so a hand-built URL 404s.
func (ch *Channel) afterPublish(ctx context.Context, msg bus.OutboundMessage, postID string) {
	link, err := ch.graphClient.GetPostPermalink(ctx, postID)
	if err != nil {
		slog.Warn("facebook: permalink lookup failed", "post_id", postID, "error", err)
	}
	ch.recordPublication(msg, postID, link)
	ch.announcePublished(msg, postID, link)
}

// afterPublishReel is afterPublish for a reel: the id is a video id and its
// permalink comes back as a facebook.com path.
func (ch *Channel) afterPublishReel(ctx context.Context, msg bus.OutboundMessage, videoID string) {
	link, err := ch.graphClient.GetReelPermalink(ctx, videoID)
	if err != nil {
		slog.Warn("facebook: reel permalink lookup failed", "video_id", videoID, "error", err)
	}
	ch.recordPublication(msg, videoID, link)
	ch.announcePublished(msg, videoID, link)
}

func (ch *Channel) announcePublished(msg bus.OutboundMessage, postID, link string) {
	notifyCh := strings.TrimSpace(msg.Metadata["notify_channel"])
	notifyChat := strings.TrimSpace(msg.Metadata["notify_chat"])
	if notifyCh == "" || notifyChat == "" {
		return
	}
	msgBus := ch.Bus()
	if msgBus == nil {
		slog.Warn("facebook: cannot announce publish, no message bus", "post_id", postID)
		return
	}

	// The first line must stay "✅ Đã đăng lên fanpage." for reels too: the
	// Discord channel matches that prefix to tell published drafts from pending.
	text := fmt.Sprintf("✅ Đã đăng lên fanpage.\npost_id: %s", postID)
	if msg.Metadata["fb_mode"] == "reels_post" {
		text = fmt.Sprintf("✅ Đã đăng lên fanpage.\nreel video_id: %s", postID)
	}
	if link != "" {
		text += "\n" + link
	}
	// The Discord channel reads this line to tell published drafts from
	// pending ones when an approver types "duyệt" without replying.
	if draftID := strings.TrimSpace(msg.Metadata["review_message_id"]); draftID != "" {
		text += "\ndraft_id: " + draftID
	}

	out := bus.OutboundMessage{Channel: notifyCh, ChatID: notifyChat, Content: text}
	if groupID := strings.TrimSpace(msg.Metadata["notify_group_id"]); groupID != "" {
		out.Metadata = map[string]string{"group_id": groupID}
	} else {
		// Review channels are group chats; without this the adapter may route
		// the notice as a DM and the approver never sees it.
		out.Metadata = map[string]string{"group_id": notifyChat}
	}
	msgBus.PublishOutbound(out)
	slog.Info("facebook: publish announced", "post_id", postID,
		"notify_channel", notifyCh, "has_link", link != "")
}
