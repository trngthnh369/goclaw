package tools

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"path/filepath"
	"strings"
	"time"

	"github.com/nextlevelbuilder/goclaw/internal/bus"
	"github.com/nextlevelbuilder/goclaw/internal/store"
)

// Reels publishing reuses the feed-post gate (message.go) for the Video
// Factory: a person approves ONE bot-authored review message - a video
// attachment plus a caption between a [caption] and a [/caption] line - and
// the gateway publishes that attachment's exact bytes with that caption. The
// model only says "APPROVED_REPLY"; it never supplies the text or the file.

// MaxReelUploadBytes caps a published Reel file; the facebook channel applies
// the same cap when it opens the file for upload.
const MaxReelUploadBytes = int64(100 << 20)

const (
	reelsTarget       = "reels"
	reelsCaptionOpen  = "[caption]"
	reelsCaptionClose = "[/caption]"
)

// unpublishedError is implemented by publisher errors raised before anything
// could have gone public - for a Reel, before the upload session was finished.
// Only those release the approval ledger, so the same draft can be approved again.
type unpublishedError interface {
	NotPublished() bool
}

// reelsCaption returns the text between the single [caption] line and the
// single [/caption] line of a reviewed message.
func reelsCaption(reviewed string) (string, error) {
	lines := strings.Split(normalizeApprovalContent(reviewed), "\n")
	open, closing := -1, -1
	for i, line := range lines {
		switch strings.TrimSpace(line) {
		case reelsCaptionOpen:
			if open >= 0 {
				return "", fmt.Errorf("the approved message has more than one %s line", reelsCaptionOpen)
			}
			open = i
		case reelsCaptionClose:
			if closing >= 0 {
				return "", fmt.Errorf("the approved message has more than one %s line", reelsCaptionClose)
			}
			closing = i
		}
	}
	if open < 0 || closing < 0 || closing < open {
		return "", fmt.Errorf("the approved message has no %s ... %s block, so it is not a publishable Reels draft",
			reelsCaptionOpen, reelsCaptionClose)
	}
	caption := strings.TrimSpace(strings.Join(lines[open+1:closing], "\n"))
	if caption == "" {
		return "", fmt.Errorf("the approved message has an empty caption block")
	}
	return caption, nil
}

// isReelsReviewDraft reports whether an outgoing message is a Reels review
// draft: one video and a caption block. The Discord channel marks such a
// message with 📝 so a ✅ or a bare "duyệt" can approve it; publishing still
// requires the chat to be a configured Reels review channel.
func isReelsReviewDraft(text string, media []bus.MediaAttachment) bool {
	if len(media) != 1 || !strings.HasPrefix(media[0].ContentType, "video/") {
		return false
	}
	_, err := reelsCaption(text)
	return err == nil
}

// postReel publishes the approved review message as a Facebook Reel.
func (t *MessageTool) postReel(ctx context.Context, channel, message string, forward bool, reason string) *Result {
	if err := t.validatePublishApprovalContext(ctx, forward, reason, reelsTarget); err != nil {
		return ErrorResult(err.Error())
	}
	if strings.TrimSpace(message) != approvedReplyPayloadToken {
		return ErrorResult(`reels publish takes message="APPROVED_REPLY": the gateway publishes the reviewed video and caption itself`)
	}
	if t.outboundDispatcher == nil {
		return ErrorResult("post action requires synchronous outbound dispatcher")
	}
	rc := store.RunContextFromCtx(ctx)
	if rc == nil || rc.AgentKey == "" {
		return ErrorResult("feed post requires an authenticated publisher agent")
	}
	caption, err := reelsCaption(rc.ReplyToContent)
	if err != nil {
		return ErrorResult(err.Error())
	}
	if rc.ReplyToMediaCount != 1 || len(rc.ReplyToMediaPaths) != 1 {
		return ErrorResult("the approved review message must carry exactly one video")
	}
	approvedSHA, err := approvedFeedPostMediaSHA(rc.ReplyToMedia, rc.ReplyToMediaCount)
	if err != nil || approvedSHA == "" {
		slog.Warn("message.reels_post_reply_media_rejected", "error", err)
		return ErrorResult("approved Discord media metadata is invalid")
	}

	// A master_sha256 line (written only by the gateway) marks a master-mode
	// draft: publish the signed master, never the attached review cut. A draft
	// without the line is a legacy one: publish the attachment's exact bytes.
	source, publishSHA, ledgerMediaSHA := "review_cut", approvedSHA, approvedSHA
	var media []bus.MediaAttachment
	var draftDir string
	switch masterLines := reelsMasterSHALines(rc.ReplyToContent); len(masterLines) {
	case 0:
		videoPath, err := t.resolvePublishMediaPath(ctx, "MEDIA:"+rc.ReplyToMediaPaths[0], "video/")
		if err != nil {
			slog.Warn("message.reels_post_media_rejected", "reason", err.Error())
			return ErrorResult("the approved video is unavailable or is not a video file")
		}
		media = []bus.MediaAttachment{{URL: videoPath, ContentType: mimeFromPath(videoPath)}}
	case 1:
		rec, dir, err := t.loadReelsDraft(ctx, approvedSHA)
		if err == nil {
			err = checkReelsDraftScope(ctx, rec, caption, masterLines[0])
		}
		if err != nil {
			// A posted draft is removed, so a second approval of the same message
			// lands here: say it was published, never "re-package" (a duplicate).
			if t.feedPostReserved(ctx, rc, channel, reelsTarget) {
				return ErrorResult("this review message was already published or is being published; operator reconciliation is required")
			}
			// Swept, lost or not matching: refuse; never fall back to the review cut.
			slog.Warn("security.reels_master_draft_rejected", "reason", err.Error(), "review_sha256", approvedSHA)
			return ErrorResult("this review expired or does not match its record; re-package the video and send a new review message")
		}
		source, publishSHA, draftDir = "master", rec.MasterSHA256, dir
		ledgerMediaSHA = approvedSHA + "\x00" + rec.MasterSHA256
		media = []bus.MediaAttachment{{URL: filepath.Join(dir, "master.mp4"), ContentType: "video/mp4"}}
	default:
		return ErrorResult("the approved message has more than one master_sha256 line")
	}
	cleanupMedia, err := t.bindFeedPostMedia(ctx, media, publishSHA, MaxReelUploadBytes)
	if err != nil {
		slog.Warn("message.reels_post_media_mismatch", "reason", "binding_failed", "source", source)
		return ErrorResult("the video does not match the approved Discord attachment")
	}
	defer cleanupMedia()

	outMsg := bus.OutboundMessage{
		Channel:  channel,
		ChatID:   reelsTarget,
		Content:  caption,
		Media:    media,
		TenantID: rc.TenantID,
		AgentID:  rc.AgentID,
		Metadata: map[string]string{
			"fb_mode":               "reels_post",
			"publisher_agent_id":    rc.AgentKey,
			"approved_media_sha256": publishSHA,
			"review_media_sha256":   approvedSHA,
			"reels_source":          source,
		},
	}
	if origCh, origChat := ToolChannelFromCtx(ctx), ToolChatIDFromCtx(ctx); origCh != "" && origChat != "" {
		outMsg.Metadata["notify_channel"] = origCh
		outMsg.Metadata["notify_chat"] = origChat
		outMsg.Metadata["review_message_id"] = rc.ReplyToMessageID
	}

	reservation, err := t.reserveFeedPost(ctx, rc, channel, reelsTarget, feedPostPayloadSHA(caption, ledgerMediaSHA))
	if err != nil {
		slog.Warn("message.reels_post_reservation_rejected", "error_type", fmt.Sprintf("%T", err))
		return ErrorResult("this review message was already published or is being published; operator reconciliation is required")
	}
	slog.Warn("message.reels_post_approval",
		"channel", channel,
		"reply_to_message_id", rc.ReplyToMessageID,
		"publisher_agent_id", rc.AgentKey,
		"media_sha256", approvedSHA,
		"source", source,
		"publish_sha256", publishSHA,
		"reason", reason,
	)
	if err := t.outboundDispatcher(ctx, outMsg); err != nil {
		var unpublished unpublishedError
		if errors.As(err, &unpublished) && unpublished.NotPublished() {
			if releaseErr := reservation.release(); releaseErr != nil {
				slog.Error("message.reels_post_reservation_release_failed", "error_type", fmt.Sprintf("%T", releaseErr))
			}
			slog.Warn("message.reels_post_not_started",
				append([]any{"error_type", fmt.Sprintf("%T", err)}, safeAPIErrorAttrs(err)...)...)
			return ErrorResult("the reel was not published: the upload failed before Facebook was asked to publish it. " +
				"Nothing is on the fanpage; the same review message can be approved again once the cause is fixed")
		}
		if markErr := reservation.mark("pending_unknown"); markErr != nil {
			slog.Error("message.reels_post_reservation_update_failed", "error_type", fmt.Sprintf("%T", markErr))
		}
		slog.Error("message.reels_post_dispatch_failed",
			append([]any{"error_type", fmt.Sprintf("%T", err)}, safeAPIErrorAttrs(err)...)...)
		return ErrorResult("reel publish failed after Facebook was asked to publish it; status is unknown and automatic retry is blocked - check the fanpage")
	}
	if err := reservation.mark("posted"); err != nil {
		slog.Error("message.reels_post_reservation_update_failed", "error_type", fmt.Sprintf("%T", err))
	}
	if draftDir != "" {
		t.removeReelsDraft(ctx, draftDir, approvedSHA)
	}
	return SilentResult(fmt.Sprintf(`{"status":"posted","channel":"%s","target":"%s"}`, channel, reelsTarget))
}

// checkReelsDraftScope ties a master-mode record to the approval at hand: the
// same tenant, the chat the review was sent to, the approved caption and the
// master named on the message's own line.
func checkReelsDraftScope(ctx context.Context, rec reelsDraftRecord, caption, lineSHA string) error {
	if rec.TenantID != reelsTenant(ctx) {
		return fmt.Errorf("draft belongs to another tenant")
	}
	if ch, chat := ToolChannelFromCtx(ctx), ToolChatIDFromCtx(ctx); rec.Channel != ch || rec.ChatID != chat {
		return fmt.Errorf("draft was sent to another chat")
	}
	if rec.CaptionSHA256 != sha256Hex(normalizeApprovalContent(caption)) {
		return fmt.Errorf("caption differs from the draft's")
	}
	if rec.MasterSHA256 != lineSHA {
		return fmt.Errorf("master_sha256 line differs from the draft's")
	}
	if age := time.Since(rec.CreatedAt); age < 0 || age > reelsDraftMaxAge {
		return fmt.Errorf("draft expired")
	}
	return nil
}
