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

	text := fmt.Sprintf("✅ Đã đăng lên fanpage.\npost_id: %s", postID)
	if link != "" {
		text += "\n" + link
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
