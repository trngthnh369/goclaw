package tools

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/google/uuid"

	"github.com/nextlevelbuilder/goclaw/internal/bus"
	"github.com/nextlevelbuilder/goclaw/internal/store"
)

// maxReviewMessageBytes mirrors the Discord single-message content limit
// enforced in channels/discord.sendMediaMessage. Duplicated to avoid a
// tools→channels import cycle; keep the two in sync.
const maxReviewMessageBytes = 2000

// reviewTextBytesWithMedia returns the byte length of the text that would be
// delivered alongside an image, or 0 when the draft carries no image (text-only
// sends are chunked by the channel adapter and are not size-limited here).
func reviewTextBytesWithMedia(message string) int {
	if !embeddedMediaPattern.MatchString(message) {
		return 0
	}
	text := strings.TrimSpace(embeddedMediaPattern.ReplaceAllString(message, ""))
	return len(text)
}

// embeddedMediaPattern matches "MEDIA:" followed by a non-whitespace path.
// Duplicated from agent.mediaPathPattern to avoid tools→agent import cycle.
var (
	embeddedMediaPattern = regexp.MustCompile(`MEDIA:\S+`)
	internalNoisePattern = regexp.MustCompile(`(?i)(?:^\s*\{.*"(?:agent|delegation_id|task_id|status)".*\}\s*$|I was unable to complete this task|CRITICAL:|\[ABORT PIPELINE\]|tool execution error:|context deadline exceeded)`)
)

const (
	contentFactoryApprovalChannelID = "1530127001602625677"
	contentFactoryFacebookChannel   = "fb-page"
	contentFactoryFacebookTarget    = "feed"
	feedPostLedgerDir               = ".goclaw/feed-post-ledger"
	feedPostStagingDir              = ".goclaw/feed-post-staging"
	maxFeedPostMediaBytes           = 25 << 20
	approvedReplyPayloadToken       = "APPROVED_REPLY"
	contentFactoryTerminalKey       = "contentfactory-terminal"
)

// structuredAPIError is implemented by remote-API errors whose text is the
// service's own diagnostic and therefore safe to log.
//
// This path used to log only the error's TYPE, which made a failed publish
// undiagnosable: "*facebook.graphAPIError" says nothing about whether the token
// was rejected, a permission was missing, or the payload was malformed. The
// caution behind that choice was still sound, though — outboundDispatcher is
// generic over every channel, so an error here can come from any transport, and
// a transport failure arrives as *url.Error whose text embeds the full request
// URL. Any channel that carries a credential in a query parameter would leak it.
//
// Opting in per error type keeps the diagnosis without betting on how every
// present and future channel builds its URLs.
type structuredAPIError interface {
	APIErrorCode() int
	APIErrorMessage() string
}

// safeAPIErrorAttrs returns loggable slog attributes describing err, or nothing
// when err carries no vetted-safe detail.
func safeAPIErrorAttrs(err error) []any {
	var apiErr structuredAPIError
	if !errors.As(err, &apiErr) {
		return nil
	}
	return []any{"api_error_code", apiErr.APIErrorCode(), "api_error_message", apiErr.APIErrorMessage()}
}

// ContentFactoryTerminalActionPending reports whether agentKey is the
// ContentFactory director and its one required terminal action — the review
// draft (or abort notice) sent to the review channel — has not happened yet in
// this run.
//
// The agent loop uses this to refuse to end a run on progress narration. That
// invariant used to live only in the director's prompt, and prompts do not hold
// it: runs on 2026-08-02 and 2026-08-05 both ended with an assistant turn that
// announced the next delegation without emitting the tool call, and both were
// recorded as status=ok with no review message ever sent.
func ContentFactoryTerminalActionPending(ctx context.Context, agentKey string) bool {
	if agentKey != contentFactoryDirectorAgentKey {
		return false
	}
	latch := OutboundActionLatchFromCtx(ctx)
	if latch == nil {
		return false
	}
	return !latch.Reserved(contentFactoryTerminalKey)
}

var articleSHAPattern = regexp.MustCompile(`(?i)(?:\[Article SHA-256:\s*|article_sha256:|article_hash:)([a-fA-F0-9]{64})\]?`)

type feedPostLedgerEntry struct {
	Version          int       `json:"version"`
	Status           string    `json:"status"`
	TenantID         string    `json:"tenant_id"`
	Channel          string    `json:"channel"`
	Target           string    `json:"target"`
	ReplyToMessageID string    `json:"reply_to_message_id"`
	PayloadSHA256    string    `json:"payload_sha256"`
	CreatedAt        time.Time `json:"created_at"`
	UpdatedAt        time.Time `json:"updated_at"`
}

type feedPostReservation struct {
	path  string
	entry feedPostLedgerEntry
}

// MessageTool allows the agent to proactively send messages to channels.
type MessageTool struct {
	workspace          string
	dataDir            string
	restrict           bool
	sender             ChannelSender
	outboundDispatcher OutboundDispatcher
	msgBus             *bus.MessageBus
	tenantChecker      ChannelTenantChecker

	approvalChannelID string
	facebookChannel   string
	facebookTarget    string
}

func NewMessageTool(workspace string, restrict bool) *MessageTool {
	return &MessageTool{
		workspace:         workspace,
		restrict:          restrict,
		approvalChannelID: contentFactoryApprovalChannelID,
		facebookChannel:   contentFactoryFacebookChannel,
		facebookTarget:    contentFactoryFacebookTarget,
	}
}

func (t *MessageTool) SetApprovalChannelID(id string) {
	if id != "" {
		t.approvalChannelID = id
	}
}

func (t *MessageTool) SetFeedPostDestination(channel, target string) {
	if channel != "" {
		t.facebookChannel = channel
	}
	if target != "" {
		t.facebookTarget = target
	}
}

func (t *MessageTool) getApprovalChannelID() string {
	if t.approvalChannelID != "" {
		return t.approvalChannelID
	}
	return contentFactoryApprovalChannelID
}

func (t *MessageTool) getFacebookChannel() string {
	if t.facebookChannel != "" {
		return t.facebookChannel
	}
	return contentFactoryFacebookChannel
}

func (t *MessageTool) getFacebookTarget() string {
	if t.facebookTarget != "" {
		return t.facebookTarget
	}
	return contentFactoryFacebookTarget
}

func (t *MessageTool) SetDataDir(dataDir string)                      { t.dataDir = dataDir }
func (t *MessageTool) SetChannelSender(s ChannelSender)               { t.sender = s }
func (t *MessageTool) SetOutboundDispatcher(d OutboundDispatcher)     { t.outboundDispatcher = d }
func (t *MessageTool) SetMessageBus(b *bus.MessageBus)                { t.msgBus = b }
func (t *MessageTool) SetChannelTenantChecker(c ChannelTenantChecker) { t.tenantChecker = c }

func (t *MessageTool) Name() string { return "message" }
func (t *MessageTool) Description() string {
	return "Send a message to a channel (Telegram, Discord, Slack, Zalo, Feishu/Lark, WhatsApp, etc.). In a DM/group, omit `target` to reply to the current chat — DO NOT set a different target unless the user explicitly asked you to forward (then set `forward=true` + `forward_reason` quoting the request). In cron/heartbeat/subagent/team contexts, set `target` per job spec."
}

func (t *MessageTool) Parameters() map[string]any {
	return map[string]any{
		"type": "object",
		"properties": map[string]any{
			"action": map[string]any{
				"type":        "string",
				"description": "Action to perform: 'send' to deliver a message, 'post' to publish to a channel feed (e.g. Facebook page)",
				"enum":        []string{"send", "post"},
			},
			"channel": map[string]any{
				"type":        "string",
				"description": "Channel name (default: current channel from context)",
			},
			"target": map[string]any{
				"type":        "string",
				"description": "Chat ID to send to (default: current chat from context)",
			},
			"message": map[string]any{
				"type":        "string",
				"description": "Message content to send. For action='post' after a structured approval reply, pass exactly 'APPROVED_REPLY' to bind the complete reviewed text and attachment natively. To send a file as attachment, use the prefix MEDIA: followed by the file path, e.g. 'MEDIA:docs/report.pdf' or 'MEDIA:/tmp/image.png'.",
			},
			"forward": map[string]any{
				"type":        "boolean",
				"description": "Set true ONLY when the user explicitly asked to forward to a different chat than the current one. Required when target ≠ current chat in DM/group sessions.",
			},
			"forward_reason": map[string]any{
				"type":        "string",
				"description": "Quote the user's literal request when forward=true (e.g. 'gửi báo cáo này sang group dev'). Required when forward=true.",
			},
			"idempotency_key": map[string]any{
				"type":        "string",
				"description": "Optional per-run idempotency key. Required for ContentFactory terminal sends to the review channel; use contentfactory-terminal.",
			},
		},
		"required": []string{"action", "message"},
	}
}

func (t *MessageTool) Execute(ctx context.Context, args map[string]any) *Result {
	action := argString(args, "action")
	if action != "send" && action != "post" {
		return ErrorResult(fmt.Sprintf("unsupported action: %s (supported: 'send', 'post')", action))
	}

	message := argString(args, "message")
	if message == "" {
		return ErrorResult("message is required")
	}

	channel := argString(args, "channel")
	if channel == "" {
		channel = ToolChannelFromCtx(ctx)
	}
	if channel == "" {
		return ErrorResult("channel is required (no current channel in context)")
	}

	target := argString(args, "target")
	if target == "" && action == "post" {
		target = t.facebookTarget
	}
	if target == "" {
		target = ToolChatIDFromCtx(ctx)
	}
	if target == "" {
		return ErrorResult("target chat ID is required (no current chat in context)")
	}
	if action == "post" && (channel != t.facebookChannel || target != t.facebookTarget) {
		return ErrorResult(fmt.Sprintf("feed post destination must be %s/%s", t.facebookChannel, t.facebookTarget))
	}
	isContentFactoryReviewTarget := action == "send" && (target == t.getApprovalChannelID() || target == contentFactoryApprovalChannelID)
	if isContentFactoryReviewTarget {
		// Every rejection below must run BEFORE the latch is reserved. The latch
		// is one-shot per run, so validating after reserving turns a recoverable
		// mistake into a run with no review message and no way to retry.
		if key := argString(args, "idempotency_key"); key != contentFactoryTerminalKey {
			return ErrorResult(fmt.Sprintf("ContentFactory review-channel sends require idempotency_key=%q", contentFactoryTerminalKey))
		}
		if internalNoisePattern.MatchString(message) {
			return ErrorResult("Internal status JSON, abort messages, and tool error tracebacks cannot be sent to the review channel. Send ONLY clean article draft content.")
		}
		// A review draft carrying an image is delivered as a single media
		// message, which fails closed above the platform limit instead of
		// chunking. Reject it here so the sender can shorten and retry.
		if n := reviewTextBytesWithMedia(message); n > maxReviewMessageBytes {
			return ErrorResult(fmt.Sprintf(
				"review draft with an image is %d bytes; the platform limit is %d. Shorten the article body — it will NOT be truncated.",
				n, maxReviewMessageBytes))
		}
		if latch := OutboundActionLatchFromCtx(ctx); latch != nil && !latch.TryReserve(contentFactoryTerminalKey) {
			return SilentResult(fmt.Sprintf(`{"status":"duplicate_suppressed","channel":"%s","target":"%s"}`, channel, target))
		}
	}

	// Self-send guard: prevent agent from sending to its own chat via message tool.
	// Text self-sends are always blocked (response goes through normal outbound).
	// MEDIA self-sends are allowed ONLY when the file was NOT already queued for
	// delivery (i.e. write_file was called with deliver=false). This prevents both
	// duplicate delivery (deliver=true then message MEDIA:) and runaway retry loops
	// (deliver=false then message MEDIA: blocked unconditionally).
	ctxChannel := ToolChannelFromCtx(ctx)
	ctxChatID := ToolChatIDFromCtx(ctx)
	forward, _ := args["forward"].(bool)
	isSelfSend := ctxChannel != "" && ctxChatID != "" && channel == ctxChannel && target == ctxChatID
	if isSelfSend && !forward {
		isMediaSend := embeddedMediaPattern.MatchString(message)
		if !isMediaSend {
			return ErrorResult("You are already responding to this chat. Your response text will be delivered automatically. Do not use the message tool to send text to your own chat — just include the content in your response text. To deliver files, use write_file with deliver=true instead.")
		}
		// MEDIA self-send: block if ALL referenced files are already queued for delivery.
		// Extracts paths from both standalone "MEDIA:path" and embedded multi-line messages.
		if dm := DeliveredMediaFromCtx(ctx); dm != nil {
			mediaRefs := embeddedMediaPattern.FindAllString(message, -1)
			allDelivered := len(mediaRefs) > 0
			for _, raw := range mediaRefs {
				if filePath, ok := t.resolveMediaPath(ctx, raw); ok {
					if !dm.IsDelivered(filePath) {
						allDelivered = false
						break
					}
				}
			}
			if allDelivered {
				return ErrorResult("This file is already queued for automatic delivery via write_file(deliver=true). Do not send it again. To deliver files that were written with deliver=false, use write_file again with deliver=true, or use message(MEDIA:path) which is allowed for undelivered files.")
			}
		}
	}

	// Tenant isolation: validate channel belongs to current tenant.
	if err := t.validateChannelTenant(ctx, channel, target); err != nil {
		return err
	}

	// Cross-target guard: in DM/group/default sessions, prevent the agent from
	// sending to a chat other than the one bound to its context. FREE session
	// kinds (cron/heartbeat/subagent/team) compose targets per job spec and
	// bypass this guard. Opt-in forwarding requires forward=true +
	// non-empty forward_reason; notice is posted back to the origin chat and
	// an slog audit line is emitted.
	sessionKey := ToolSessionKeyFromCtx(ctx)
	var forwardReason string // non-empty ⇒ guard allowed a cross-target forward; post notice on success
	if MessageTargetEnforced(sessionKey) {
		crossTarget := channel != ctxChannel || target != ctxChatID
		if crossTarget && (ctxChannel != "" || ctxChatID != "") {
			forward, _ := args["forward"].(bool)
			reason := strings.TrimSpace(argString(args, "forward_reason"))
			if !forward || reason == "" {
				return ErrorResult(fmt.Sprintf(
					"Cross-target send blocked. You are bound to %s/%s but tried to send to %s/%s. "+
						"If the user explicitly asked you to forward, retry with forward=true AND forward_reason=\"<quote user's literal request>\".",
					ctxChannel, ctxChatID, channel, target))
			}
			slog.Warn("message.cross_target_forward",
				"session", sessionKey,
				"from_channel", ctxChannel, "from", ctxChatID,
				"to_channel", channel, "to", target,
				"reason", reason)
			forwardReason = reason
		}
	}
	// noticeOnSuccess posts the cross-target breadcrumb back to origin iff the
	// forward succeeded (res.IsError == false). Guarantees we never announce
	// a fake delivery when the downstream sender/bus publish fails.
	noticeOnSuccess := func(res *Result) *Result {
		if forwardReason != "" && res != nil && !res.IsError {
			t.postCrossTargetNotice(ctx, target, forwardReason)
		}
		return res
	}

	// action="post": publish the exact Discord-reviewed payload to the fixed
	// ContentFactory Facebook destination. Reservation happens before dispatch so
	// duplicate approvals and ambiguous Graph API outcomes fail closed.
	if action == "post" {
		forward, _ := args["forward"].(bool)
		reason := strings.TrimSpace(argString(args, "forward_reason"))
		if err := t.validateFeedPostApprovalContext(ctx, forward, reason); err != nil {
			return ErrorResult(err.Error())
		}
		if t.outboundDispatcher == nil {
			return ErrorResult("post action requires synchronous outbound dispatcher")
		}

		rc := store.RunContextFromCtx(ctx)
		if rc == nil || rc.AgentKey == "" {
			return ErrorResult("feed post requires an authenticated publisher agent")
		}
		outMsg := bus.OutboundMessage{
			Channel:  channel,
			ChatID:   target,
			Content:  message,
			TenantID: rc.TenantID,
			AgentID:  rc.AgentID,
			Metadata: map[string]string{
				"fb_mode":            "feed_post",
				"publisher_agent_id": rc.AgentKey,
			},
		}
		// Tell the publisher where to announce success. The post id is created
		// inside the channel and cannot come back here — OutboundDispatcher
		// returns only an error — so the channel has to send the confirmation
		// itself. Without this the approver gets nothing after replying "duyệt":
		// the director is instructed to answer NO_REPLY once its terminal tool
		// returns, and the only message that did go out was a generic
		// cross-target forward breadcrumb that never mentions publishing.
		if origCh, origChat := ToolChannelFromCtx(ctx), ToolChatIDFromCtx(ctx); origCh != "" && origChat != "" {
			outMsg.Metadata["notify_channel"] = origCh
			outMsg.Metadata["notify_chat"] = origChat
		}
		if strings.TrimSpace(message) == approvedReplyPayloadToken {
			if strings.TrimSpace(rc.ReplyToContent) == "" {
				return ErrorResult("approved Discord reply has no publishable content")
			}
			outMsg.Content = rc.ReplyToContent
			if len(rc.ReplyToMediaPaths) > 1 {
				return ErrorResult("approved Discord reply has too many bound media files")
			}
			if len(rc.ReplyToMediaPaths) == 1 {
				resolved, err := t.resolveFeedPostMediaPath(ctx, "MEDIA:"+rc.ReplyToMediaPaths[0])
				if err != nil {
					return ErrorResult("approved Discord media path is unavailable")
				}
				outMsg.Media = []bus.MediaAttachment{{
					URL:         resolved,
					ContentType: mimeFromPath(resolved),
				}}
			}
		} else if strings.Contains(message, "MEDIA:") {
			cleanMsg, embeddedMedia, err := t.extractFeedPostMedia(ctx, message)
			if err != nil {
				slog.Warn("message.feed_post_media_rejected", "reason", "validation_failed")
				return ErrorResult("feed post media validation failed")
			}
			outMsg.Content = cleanMsg
			outMsg.Media = embeddedMedia
		}

		if !feedPostContentMatchesApproval(outMsg.Content, rc.ReplyToContent, rc.ReplyToMedia) {
			return ErrorResult("feed post content does not match the approved Discord reply")
		}
		approvedMediaSHA, err := approvedFeedPostMediaSHA(rc.ReplyToMedia, rc.ReplyToMediaCount)
		if err != nil {
			slog.Warn("message.feed_post_reply_media_rejected", "error", err)
			return ErrorResult("approved Discord media metadata is invalid")
		}
		cleanupMedia, err := t.bindFeedPostMedia(ctx, outMsg.Media, approvedMediaSHA)
		if err != nil {
			slog.Warn("message.feed_post_media_mismatch", "reason", "binding_failed")
			return ErrorResult("feed post media does not match the approved Discord reply")
		}
		defer cleanupMedia()
		if approvedMediaSHA != "" {
			outMsg.Metadata["approved_media_sha256"] = approvedMediaSHA
		}

		payloadSHA := feedPostPayloadSHA(outMsg.Content, approvedMediaSHA)
		reservation, err := t.reserveFeedPost(ctx, rc, channel, target, payloadSHA)
		if err != nil {
			slog.Warn("message.feed_post_reservation_rejected", "error_type", fmt.Sprintf("%T", err))
			return ErrorResult("feed post is already reserved; operator reconciliation is required")
		}

		slog.Warn("message.feed_post_approval",
			"channel", channel,
			"target", target,
			"reply_to_message_id", rc.ReplyToMessageID,
			"publisher_agent_id", rc.AgentKey,
			"reason", reason,
		)
		if err := t.outboundDispatcher(ctx, outMsg); err != nil {
			if markErr := reservation.mark("pending_unknown"); markErr != nil {
				slog.Error("message.feed_post_reservation_update_failed", "error_type", fmt.Sprintf("%T", markErr))
			}
			slog.Error("message.feed_post_dispatch_failed",
				append([]any{"error_type", fmt.Sprintf("%T", err)}, safeAPIErrorAttrs(err)...)...)
			return ErrorResult("feed post failed; status is unknown and automatic retry is blocked")
		}
		if err := reservation.mark("posted"); err != nil {
			slog.Error("message.feed_post_reservation_update_failed", "error_type", fmt.Sprintf("%T", err))
		}
		return noticeOnSuccess(SilentResult(fmt.Sprintf(`{"status":"posted","channel":"%s"}`, channel)))
	}

	// Handle MEDIA: prefix — send file as attachment instead of text.
	if filePath, ok := t.resolveMediaPath(ctx, message); ok {
		return noticeOnSuccess(t.sendMedia(ctx, channel, target, filePath))
	}

	// Extract embedded MEDIA: paths from multi-line messages.
	// LLMs may include MEDIA: in conversational text rather than as a standalone prefix.
	message, embeddedMedia := t.extractEmbeddedMedia(ctx, message)

	// If we found embedded media and bus is available, prefer bus path (supports media attachments).
	if len(embeddedMedia) > 0 && t.msgBus != nil {
		outMsg := bus.OutboundMessage{
			Channel: channel,
			ChatID:  target,
			Content: message,
			Media:   embeddedMedia,
		}
		if isGroupContext(ctx) {
			outMsg.Metadata = map[string]string{"group_id": target}
		}
		t.msgBus.PublishOutbound(outMsg)
		// Mark each embedded media path as delivered.
		if dm := DeliveredMediaFromCtx(ctx); dm != nil {
			for _, att := range embeddedMedia {
				dm.Mark(att.URL)
			}
		}
		return noticeOnSuccess(SilentResult(fmt.Sprintf(`{"status":"sent","channel":"%s","target":"%s"}`, channel, target)))
	}

	// Prefer direct channel sender for immediate delivery.
	// For group chats, fall through to message bus which supports metadata.
	if t.sender != nil && !isGroupContext(ctx) {
		if err := t.sender(ctx, channel, target, message); err != nil {
			return ErrorResult(fmt.Sprintf("failed to send message: %v", err))
		}
		return noticeOnSuccess(SilentResult(fmt.Sprintf(`{"status":"sent","channel":"%s","target":"%s"}`, channel, target)))
	}

	// Publish via message bus outbound queue.
	// Group messages include metadata so channel implementations (e.g. Zalo)
	// can distinguish group sends from DMs.
	if t.msgBus != nil {
		outMsg := bus.OutboundMessage{
			Channel: channel,
			ChatID:  target,
			Content: message,
		}
		if isGroupContext(ctx) {
			outMsg.Metadata = map[string]string{"group_id": target}
		}
		t.msgBus.PublishOutbound(outMsg)
		return noticeOnSuccess(SilentResult(fmt.Sprintf(`{"status":"sent","channel":"%s","target":"%s"}`, channel, target)))
	}

	// Last resort: direct sender without group metadata.
	if t.sender != nil {
		if err := t.sender(ctx, channel, target, message); err != nil {
			return ErrorResult(fmt.Sprintf("failed to send message: %v", err))
		}
		return noticeOnSuccess(SilentResult(fmt.Sprintf(`{"status":"sent","channel":"%s","target":"%s"}`, channel, target)))
	}

	return ErrorResult("no channel sender or message bus available")
}

var (
	positiveApprovalPattern = regexp.MustCompile(`(?i)^(ok\s+|đã\s+)?(duyệt|approve|đăng|post)(d?|d\s+this|\s+(bài\s+này|bài|này|nhé|ngay|đi|nha|this))?$`)
	negationPattern         = regexp.MustCompile(`(?i)\b(chưa|không|ko|k|đừng|hủy|bỏ|sửa|edit|change|từ\s+chối|no|don'?t|stop)\b`)
)

func isPositiveFeedPostApproval(message string) bool {
	cmd := strings.ToLower(strings.TrimSpace(message))
	cmd = strings.Trim(cmd, " \t\r\n.!✅👍")
	if negationPattern.MatchString(cmd) {
		return false
	}
	return positiveApprovalPattern.MatchString(cmd)
}

func feedPostContentMatchesApproval(postContent, replyContent, replyMedia string) bool {
	postNormalized := normalizeApprovalContent(postContent)
	if postNormalized == "" {
		return false
	}
	replyNormalized := normalizeApprovalContent(replyContent)
	if replyNormalized != "" && postNormalized == replyNormalized {
		return true
	}

	// Go-computed SHA-256 hex digest match
	h := sha256.Sum256([]byte(postNormalized))
	postSHA := hex.EncodeToString(h[:])

	// Match tag embedded in replyContent: [Article SHA-256: <hex>], article_sha256:<hex>, etc.
	if match := articleSHAPattern.FindStringSubmatch(replyContent); len(match) == 2 {
		if strings.EqualFold(match[1], postSHA) {
			return true
		}
	}

	// Match attached .md / .txt document hash in replyMedia
	if replyMedia != "" {
		lines := strings.Split(replyMedia, "\n")
		for _, line := range lines {
			line = strings.TrimSpace(line)
			if line == "" {
				continue
			}
			sep := strings.LastIndexByte(line, '=')
			if sep <= 0 || sep >= len(line)-1 {
				continue
			}
			filename := strings.ToLower(strings.TrimSpace(line[:sep]))
			digest := strings.ToLower(strings.TrimSpace(line[sep+1:]))
			if (strings.HasSuffix(filename, ".md") || strings.HasSuffix(filename, ".txt")) && len(digest) == 64 {
				if strings.EqualFold(digest, postSHA) {
					return true
				}
			}
		}
	}

	return false
}

func normalizeApprovalContent(s string) string {
	s = strings.ReplaceAll(s, "\r\n", "\n")
	s = strings.ReplaceAll(s, "\r", "\n")
	return strings.TrimSpace(s)
}

func approvedFeedPostMediaSHA(replyMedia string, attachmentCount int) (string, error) {
	if attachmentCount < 0 {
		return "", fmt.Errorf("invalid attachment count")
	}
	if replyMedia == "" {
		if attachmentCount == 0 {
			return "", nil
		}
		return "", fmt.Errorf("approved media digest missing")
	}

	lines := strings.Split(strings.TrimSpace(replyMedia), "\n")
	var imageEntries []string
	for _, line := range lines {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		sep := strings.LastIndexByte(line, '=')
		if sep <= 0 || sep >= len(line)-1 {
			continue
		}
		filename := strings.ToLower(strings.TrimSpace(line[:sep]))
		if !strings.HasSuffix(filename, ".md") && !strings.HasSuffix(filename, ".txt") {
			imageEntries = append(imageEntries, line)
		}
	}

	if len(imageEntries) == 0 {
		return "", nil
	}
	if len(imageEntries) > 1 {
		return "", fmt.Errorf("expected at most one approved media attachment")
	}

	entry := imageEntries[0]
	separator := strings.LastIndexByte(entry, '=')
	if separator <= 0 || separator >= len(entry)-1 {
		return "", fmt.Errorf("invalid approved media digest")
	}
	digest := strings.ToLower(strings.TrimSpace(entry[separator+1:]))
	if len(digest) != sha256.Size*2 {
		return "", fmt.Errorf("invalid approved media digest")
	}
	decoded, err := hex.DecodeString(digest)
	if err != nil || len(decoded) != sha256.Size {
		return "", fmt.Errorf("invalid approved media digest")
	}
	return digest, nil
}

func (t *MessageTool) bindFeedPostMedia(
	ctx context.Context,
	media []bus.MediaAttachment,
	approvedSHA string,
) (func(), error) {
	if len(media) == 0 {
		if approvedSHA != "" {
			return nil, fmt.Errorf("approved payload has media but post does not")
		}
		return func() {}, nil
	}
	if len(media) != 1 || approvedSHA == "" {
		return nil, fmt.Errorf("post media count does not match approved payload")
	}

	preInfo, err := os.Lstat(media[0].URL)
	if err != nil || preInfo.Mode()&os.ModeSymlink != 0 || !preInfo.Mode().IsRegular() {
		return nil, fmt.Errorf("approved media source is not a regular file")
	}
	source, err := os.Open(media[0].URL)
	if err != nil {
		return nil, fmt.Errorf("open approved media: %w", err)
	}
	defer source.Close()
	openedInfo, err := source.Stat()
	if err != nil || !openedInfo.Mode().IsRegular() || !os.SameFile(preInfo, openedInfo) {
		return nil, fmt.Errorf("approved media identity changed")
	}
	if openedInfo.Size() > maxFeedPostMediaBytes {
		return nil, fmt.Errorf("approved media exceeds size limit")
	}

	stateRoot := t.feedPostStateRoot(ctx)
	if stateRoot == "" {
		return nil, fmt.Errorf("gateway data directory unavailable for media staging")
	}
	stagingDir := filepath.Join(stateRoot, filepath.FromSlash(feedPostStagingDir))
	if err := os.MkdirAll(stagingDir, 0o700); err != nil {
		return nil, fmt.Errorf("create media staging directory: %w", err)
	}
	staged, err := os.CreateTemp(stagingDir, "upload-*.media")
	if err != nil {
		return nil, fmt.Errorf("create media snapshot: %w", err)
	}
	stagedPath := staged.Name()
	cleanup := func() { _ = os.Remove(stagedPath) }
	if err := staged.Chmod(0o600); err != nil {
		staged.Close()
		cleanup()
		return nil, fmt.Errorf("secure media snapshot: %w", err)
	}

	h := sha256.New()
	written, copyErr := io.Copy(io.MultiWriter(staged, h), io.LimitReader(source, maxFeedPostMediaBytes+1))
	if copyErr != nil || written > maxFeedPostMediaBytes {
		staged.Close()
		cleanup()
		return nil, fmt.Errorf("snapshot approved media")
	}
	if err := staged.Sync(); err != nil {
		staged.Close()
		cleanup()
		return nil, fmt.Errorf("sync media snapshot: %w", err)
	}
	if err := staged.Close(); err != nil {
		cleanup()
		return nil, fmt.Errorf("close media snapshot: %w", err)
	}
	if actualSHA := hex.EncodeToString(h.Sum(nil)); actualSHA != approvedSHA {
		cleanup()
		return nil, fmt.Errorf("post media digest mismatch")
	}

	media[0].URL = stagedPath
	return cleanup, nil
}

func hashFeedPostFile(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()

	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

func feedPostPayloadSHA(content, mediaSHA string) string {
	h := sha256.New()
	_, _ = io.WriteString(h, normalizeApprovalContent(content))
	_, _ = io.WriteString(h, "\x00")
	_, _ = io.WriteString(h, mediaSHA)
	return hex.EncodeToString(h.Sum(nil))
}

func (t *MessageTool) feedPostStateRoot(ctx context.Context) string {
	if t.dataDir != "" {
		return t.dataDir
	}
	if teamRoot := ToolTeamRootFromCtx(ctx); teamRoot != "" {
		return teamRoot
	}
	return t.workspace
}

func (t *MessageTool) reserveFeedPost(
	ctx context.Context,
	rc *store.RunContext,
	channel string,
	target string,
	payloadSHA string,
) (*feedPostReservation, error) {
	ledgerRoot := t.feedPostStateRoot(ctx)
	if ledgerRoot == "" {
		return nil, fmt.Errorf("gateway data directory unavailable for feed post ledger")
	}
	ledgerDir := filepath.Join(ledgerRoot, filepath.FromSlash(feedPostLedgerDir))
	if err := os.MkdirAll(ledgerDir, 0o700); err != nil {
		return nil, fmt.Errorf("create feed post ledger: %w", err)
	}

	keyHash := sha256.Sum256([]byte(strings.Join([]string{
		rc.TenantID.String(),
		channel,
		target,
		rc.ReplyToMessageID,
	}, "\x00")))
	path := filepath.Join(ledgerDir, hex.EncodeToString(keyHash[:])+".json")
	now := time.Now().UTC()
	entry := feedPostLedgerEntry{
		Version:          1,
		Status:           "pending",
		TenantID:         rc.TenantID.String(),
		Channel:          channel,
		Target:           target,
		ReplyToMessageID: rc.ReplyToMessageID,
		PayloadSHA256:    payloadSHA,
		CreatedAt:        now,
		UpdatedAt:        now,
	}

	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
		if os.IsExist(err) {
			return nil, fmt.Errorf("feed post reservation already exists")
		}
		return nil, fmt.Errorf("create feed post reservation: %w", err)
	}
	reservation := &feedPostReservation{path: path, entry: entry}
	if err := json.NewEncoder(f).Encode(entry); err != nil {
		f.Close()
		_ = os.Remove(path)
		return nil, fmt.Errorf("write feed post reservation: %w", err)
	}
	if err := f.Sync(); err != nil {
		f.Close()
		_ = os.Remove(path)
		return nil, fmt.Errorf("sync feed post reservation: %w", err)
	}
	if err := f.Close(); err != nil {
		_ = os.Remove(path)
		return nil, fmt.Errorf("close feed post reservation: %w", err)
	}
	return reservation, nil
}

func (r *feedPostReservation) mark(status string) error {
	r.entry.Status = status
	r.entry.UpdatedAt = time.Now().UTC()
	f, err := os.OpenFile(r.path, os.O_WRONLY|os.O_TRUNC, 0o600)
	if err != nil {
		return err
	}
	if err := json.NewEncoder(f).Encode(r.entry); err != nil {
		f.Close()
		return err
	}
	if err := f.Sync(); err != nil {
		f.Close()
		return err
	}
	return f.Close()
}

func (t *MessageTool) validateFeedPostApprovalContext(ctx context.Context, forward bool, reason string) error {
	if !forward || reason == "" {
		return fmt.Errorf("feed post requires explicit forward=true and forward_reason quoting the user's approval")
	}
	if !MessageTargetEnforced(ToolSessionKeyFromCtx(ctx)) {
		return fmt.Errorf("feed post requires a live user approval session")
	}
	ctxChannel := ToolChannelFromCtx(ctx)
	ctxChatID := ToolChatIDFromCtx(ctx)
	if ctxChannel == "" || ctxChatID == "" {
		return fmt.Errorf("feed post requires an origin channel and chat for approval evidence")
	}
	if ctxChatID != t.getApprovalChannelID() {
		return fmt.Errorf("feed post approval must originate from the ContentFactory review channel")
	}
	channelType := ToolChannelTypeFromCtx(ctx)
	if channelType != "" && channelType != "discord" {
		return fmt.Errorf("feed post approval must originate from Discord")
	}
	if channelType == "" && !strings.Contains(strings.ToLower(ctxChannel), "discord") {
		return fmt.Errorf("feed post approval must originate from Discord")
	}
	rc := store.RunContextFromCtx(ctx)
	if rc == nil || strings.TrimSpace(rc.SenderID) == "" {
		return fmt.Errorf("feed post approval requires an authenticated sender")
	}
	if !rc.ApprovalSenderAllowed {
		return fmt.Errorf("feed post approval sender is not explicitly allowlisted")
	}
	if strings.TrimSpace(rc.ReplyToMessageID) == "" {
		return fmt.Errorf("feed post approval must be a structured reply to reviewed content")
	}
	if strings.TrimSpace(rc.ChannelBotUserID) == "" || rc.ReplyToAuthorID != rc.ChannelBotUserID {
		return fmt.Errorf("feed post approval must reply to a bot-authored review message")
	}
	if !rc.ReplyToMediaComplete {
		return fmt.Errorf("feed post approval media evidence is incomplete")
	}
	if !isPositiveFeedPostApproval(rc.CurrentMessage) {
		return fmt.Errorf("feed post requires a positive approval command in the current message")
	}
	lowerReason := strings.ToLower(reason)
	if !strings.Contains(lowerReason, "duyệt") &&
		!strings.Contains(lowerReason, "approve") &&
		!strings.Contains(lowerReason, "đăng") &&
		!strings.Contains(lowerReason, "post") {
		return fmt.Errorf("feed post approval reason must quote the user's approval command")
	}
	return nil
}

// validateChannelTenant checks the target channel belongs to the current tenant.
// Returns an error Result if the send should be blocked, nil if allowed.
func (t *MessageTool) validateChannelTenant(ctx context.Context, channel, target string) *Result {
	if t.tenantChecker == nil {
		return nil
	}
	chTenant, chExists := t.tenantChecker(channel)
	if !chExists {
		return ErrorResult(fmt.Sprintf("channel %q not found", channel))
	}
	// Allow: legacy/config-based channels (zero tenant) or master tenant context (system ops).
	if chTenant == uuid.Nil {
		return nil
	}
	ctxTenant := store.TenantIDFromContext(ctx)
	if ctxTenant == uuid.Nil {
		return nil // master tenant / system context
	}
	if chTenant != ctxTenant {
		slog.Warn("security.cross_tenant_send_blocked",
			"channel", channel, "target", target,
			"ctx_tenant", ctxTenant, "ch_tenant", chTenant)
		return ErrorResult("channel not accessible from this tenant")
	}
	return nil
}

// sendMedia sends a file as a media attachment via the outbound message bus.
func (t *MessageTool) sendMedia(ctx context.Context, channel, target, filePath string) *Result {
	if _, err := os.Stat(filePath); err != nil {
		return ErrorResult(fmt.Sprintf("file not found: %s", filePath))
	}
	if t.msgBus == nil {
		return ErrorResult("media sending requires message bus")
	}

	// Build metadata for group routing (Zalo needs group_id to choose group API).
	var meta map[string]string
	if isGroupContext(ctx) {
		meta = map[string]string{"group_id": target}
	}

	t.msgBus.PublishOutbound(bus.OutboundMessage{
		Channel:  channel,
		ChatID:   target,
		Media:    []bus.MediaAttachment{{URL: filePath, ContentType: mimeFromPath(filePath)}},
		Metadata: meta,
	})
	// Mark delivered so subsequent send_file or message(MEDIA:) calls detect the duplicate.
	if dm := DeliveredMediaFromCtx(ctx); dm != nil {
		dm.Mark(filePath)
	}
	out, _ := json.Marshal(map[string]string{
		"status":  "sent",
		"channel": channel,
		"target":  target,
		"media":   filepath.Base(filePath),
	})
	return SilentResult(string(out))
}

// extractFeedPostMedia scans a feed post body for MEDIA: image references.
// Unlike ordinary message sends, feed posts fail closed: every MEDIA token must
// resolve to a workspace-owned regular image file before any public post occurs.
func (t *MessageTool) extractFeedPostMedia(ctx context.Context, message string) (string, []bus.MediaAttachment, error) {
	lines := strings.Split(message, "\n")
	var cleaned []string
	var media []bus.MediaAttachment

	for _, line := range lines {
		trimmed := strings.TrimSpace(line)
		matches := embeddedMediaPattern.FindAllString(trimmed, -1)
		if len(matches) == 0 {
			if strings.Contains(trimmed, "MEDIA:") {
				return "", nil, fmt.Errorf("malformed MEDIA token")
			}
			cleaned = append(cleaned, line)
			continue
		}
		for _, raw := range matches {
			resolved, err := t.resolveFeedPostMediaPath(ctx, raw)
			if err != nil {
				return "", nil, err
			}
			media = append(media, bus.MediaAttachment{
				URL:         resolved,
				ContentType: mimeFromPath(resolved),
			})
		}
		remainder := strings.TrimSpace(embeddedMediaPattern.ReplaceAllString(line, ""))
		if strings.Contains(remainder, "MEDIA:") {
			return "", nil, fmt.Errorf("malformed MEDIA token")
		}
		if remainder != "" {
			cleaned = append(cleaned, remainder)
		}
	}

	if len(media) > 1 {
		return "", nil, fmt.Errorf("feed posts support exactly one image attachment, got %d", len(media))
	}
	return strings.TrimSpace(strings.Join(cleaned, "\n")), media, nil
}

func (t *MessageTool) resolveFeedPostMediaPath(ctx context.Context, s string) (string, error) {
	filePath, ok := t.resolveMediaPath(ctx, s)
	if !ok {
		return "", fmt.Errorf("invalid MEDIA path")
	}
	contentType := mimeFromPath(filePath)
	if !strings.HasPrefix(contentType, "image/") {
		return "", fmt.Errorf("feed posts only support image media, got %s", contentType)
	}
	info, err := os.Lstat(filePath)
	if err != nil {
		return "", fmt.Errorf("stat media file: %w", err)
	}
	if info.Mode()&os.ModeSymlink != 0 {
		return "", fmt.Errorf("media file must not be a symlink")
	}
	if !info.Mode().IsRegular() {
		return "", fmt.Errorf("media file must be a regular file")
	}
	if err := checkHardlink(filePath); err != nil {
		return "", err
	}

	workspace := ToolWorkspaceFromCtx(ctx)
	if workspace == "" {
		workspace = t.workspace
	}
	if effectiveRestrict(ctx, t.restrict) {
		if workspace == "" {
			return "", fmt.Errorf("workspace is required for feed post media")
		}
		wsReal, err := filepath.EvalSymlinks(workspace)
		if err != nil {
			return "", fmt.Errorf("resolve workspace: %w", err)
		}
		realPath, err := filepath.EvalSymlinks(filePath)
		if err != nil {
			return "", fmt.Errorf("resolve media file: %w", err)
		}
		if !isPathInside(realPath, wsReal) {
			return "", fmt.Errorf("media file outside workspace")
		}
		return realPath, nil
	}
	return filePath, nil
}

// extractEmbeddedMedia scans a multi-line message for embedded MEDIA: path references.
// Returns cleaned text (MEDIA: lines removed) and resolved media attachments.
// Prevents raw MEDIA: paths from leaking to channels when LLMs embed them
// in conversational text instead of using a standalone MEDIA: prefix.
func (t *MessageTool) extractEmbeddedMedia(ctx context.Context, message string) (string, []bus.MediaAttachment) {
	if !strings.Contains(message, "MEDIA:") {
		return message, nil
	}

	lines := strings.Split(message, "\n")
	var cleaned []string
	var media []bus.MediaAttachment

	for _, line := range lines {
		trimmed := strings.TrimSpace(line)
		// Skip [[audio_as_voice]] tags (TTS voice messages).
		if strings.HasPrefix(trimmed, "[[audio_as_voice]]") {
			continue
		}
		// Find all MEDIA: tokens on this line.
		matches := embeddedMediaPattern.FindAllString(trimmed, -1)
		if len(matches) == 0 {
			cleaned = append(cleaned, line)
			continue
		}
		// Extract each MEDIA: path and resolve via security-checked path resolution.
		for _, raw := range matches {
			if resolved, ok := t.resolveMediaPath(ctx, raw); ok {
				media = append(media, bus.MediaAttachment{
					URL:         resolved,
					ContentType: mimeFromPath(resolved),
				})
			}
		}
		// Strip MEDIA: tokens from line, keep surrounding text.
		remainder := strings.TrimSpace(embeddedMediaPattern.ReplaceAllString(line, ""))
		if remainder != "" {
			cleaned = append(cleaned, remainder)
		}
	}

	return strings.TrimSpace(strings.Join(cleaned, "\n")), media
}

// mimeFromPath returns a MIME type based on file extension.
// Duplicated from agent.mimeFromExt to avoid tools→agent import cycle.
func mimeFromPath(path string) string {
	switch strings.ToLower(filepath.Ext(path)) {
	case ".png":
		return "image/png"
	case ".jpg", ".jpeg":
		return "image/jpeg"
	case ".gif":
		return "image/gif"
	case ".webp":
		return "image/webp"
	case ".mp4":
		return "video/mp4"
	case ".ogg", ".opus":
		return "audio/ogg"
	case ".mp3":
		return "audio/mpeg"
	case ".wav":
		return "audio/wav"
	case ".pdf":
		return "application/pdf"
	case ".doc":
		return "application/msword"
	case ".docx":
		return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
	case ".xls":
		return "application/vnd.ms-excel"
	case ".xlsx":
		return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
	default:
		return "application/octet-stream"
	}
}

// argString reads a tool argument as a non-empty string. LLM tool JSON often encodes
// numeric chat IDs as JSON numbers (float64); a plain .(string) type assert would
// ignore them and fall back to context — wrong for proactive sends to a group.
func argString(m map[string]any, key string) string {
	v, ok := m[key]
	if !ok || v == nil {
		return ""
	}
	switch s := v.(type) {
	case string:
		return strings.TrimSpace(s)
	case float64:
		if s != s { // NaN
			return ""
		}
		// Telegram chat IDs are integers; json.Unmarshal uses float64 for all numbers.
		if s == float64(int64(s)) {
			return strconv.FormatInt(int64(s), 10)
		}
		return strings.TrimSpace(fmt.Sprintf("%.0f", s))
	case int:
		return strconv.FormatInt(int64(s), 10)
	case int64:
		return strconv.FormatInt(s, 10)
	case json.Number:
		return strings.TrimSpace(string(s))
	default:
		return strings.TrimSpace(fmt.Sprint(s))
	}
}

// isGroupContext returns true if the current context indicates a group conversation.
func isGroupContext(ctx context.Context) bool {
	userID := store.UserIDFromContext(ctx)
	return ToolPeerKindFromCtx(ctx) == "group" ||
		strings.HasPrefix(userID, "group:") ||
		strings.HasPrefix(userID, "guild:")
}

// resolveMediaPath extracts and validates a file path from a "MEDIA:path" string.
// Uses the same workspace-aware path resolution as other filesystem tools.
// Multi-tenant isolation forces MEDIA: paths through restricted resolution
// first, with one explicit fallback for generated media artifacts under /tmp/.
// In practice MEDIA: paths may resolve to:
//   - files inside the agent workspace
//   - absolute paths under /tmp/ for generated media artifacts
//
// Relative paths are resolved against the agent's workspace.
func (t *MessageTool) resolveMediaPath(ctx context.Context, s string) (string, bool) {
	s = strings.TrimSpace(s)
	if !strings.HasPrefix(s, "MEDIA:") {
		return "", false
	}
	raw := strings.TrimSpace(s[len("MEDIA:"):])
	if raw == "" || raw == "." {
		return "", false
	}

	workspace := ToolWorkspaceFromCtx(ctx)
	if workspace == "" {
		workspace = t.workspace
	}
	restrict := effectiveRestrict(ctx, t.restrict)

	// resolvePath handles relative→absolute, symlink, hardlink, boundary checks.
	resolved, err := resolvePath(raw, workspace, restrict)
	if err != nil {
		// When restricted, also allow /tmp/ paths (used by create_image, create_audio, etc.)
		// But reject paths that are siblings of the workspace — these are likely traversal
		// attacks where workspace/../X resolves inside /tmp/ because workspace itself is in /tmp/.
		cleaned := filepath.Clean(raw)
		wsParent := filepath.Dir(filepath.Clean(workspace))
		if restrict && isInTempDir(cleaned) && !isPathInside(cleaned, wsParent) {
			return cleaned, true
		}
		return "", false
	}

	return resolved, true
}

// isInTempDir checks whether an absolute path is inside os.TempDir().
func isInTempDir(path string) bool {
	cleaned := filepath.Clean(path)
	if !filepath.IsAbs(cleaned) {
		return false
	}
	tmpDir := filepath.Clean(os.TempDir())
	return strings.HasPrefix(cleaned, tmpDir+string(filepath.Separator))
}

// CleanStagedMedia removes files older than maxAge from the feed post staging directory.
func CleanStagedMedia(stagingDir string, maxAge time.Duration) (int, error) {
	if stagingDir == "" {
		return 0, nil
	}
	entries, err := os.ReadDir(stagingDir)
	if err != nil {
		if os.IsNotExist(err) {
			return 0, nil
		}
		return 0, err
	}
	now := time.Now()
	removed := 0
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".media") {
			continue
		}
		info, err := entry.Info()
		if err != nil {
			continue
		}
		if now.Sub(info.ModTime()) > maxAge {
			if err := os.Remove(filepath.Join(stagingDir, entry.Name())); err == nil {
				removed++
			}
		}
	}
	return removed, nil
}
