package channels

import (
	"context"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"regexp"
	"strings"

	"github.com/google/uuid"
	"github.com/nextlevelbuilder/goclaw/internal/bus"
	"github.com/nextlevelbuilder/goclaw/internal/store"
)

// WebhookRoute holds a path and handler pair for mounting on the main gateway mux.
type WebhookRoute struct {
	Path    string
	Handler http.Handler
}

// dispatchOutbound consumes outbound messages from the bus and routes them
// to the appropriate channel. Internal channels are silently skipped.
func (m *Manager) dispatchOutbound(ctx context.Context) {
	slog.Info("outbound dispatcher started")

	for {
		select {
		case <-ctx.Done():
			slog.Info("outbound dispatcher stopped")
			return
		default:
			msg, ok := m.bus.SubscribeOutbound(ctx)
			if !ok {
				continue
			}

			// Skip internal channels
			if IsInternalChannel(msg.Channel) {
				msg.Deliver(nil) // intentionally not sent — not a failure
				continue
			}

			m.mu.RLock()
			channel, exists := m.channels[msg.Channel]
			m.mu.RUnlock()

			if !exists {
				slog.Warn("unknown channel for outbound message", "channel", msg.Channel)
				msg.Deliver(fmt.Errorf("unknown channel %q", msg.Channel))
				continue
			}

			// Filter out temp media files that no longer exist (already sent by another dispatch).
			if len(msg.Media) > 0 {
				tmpDir := os.TempDir()
				filtered := msg.Media[:0]
				for _, media := range msg.Media {
					if media.URL != "" && strings.HasPrefix(media.URL, tmpDir) {
						if _, err := os.Stat(media.URL); err != nil {
							slog.Debug("skipping already-delivered temp media", "path", media.URL)
							continue
						}
					}
					filtered = append(filtered, media)
				}
				msg.Media = filtered
				// If only media was in this message and all files are gone, skip entirely.
				if len(msg.Media) == 0 && msg.Content == "" {
					msg.Deliver(nil) // already delivered by an earlier dispatch
					continue
				}
			}

			// Add tenant context for per-tenant TTS auto-apply
			sendCtx := ctx
			if msg.TenantID != uuid.Nil {
				sendCtx = store.WithTenantID(ctx, msg.TenantID)
			}

			// Add agent audio context for per-agent TTS voice override
			if msg.AgentID != uuid.Nil && len(msg.AgentOtherConfig) > 0 {
				sendCtx = store.WithAgentAudio(sendCtx, store.AgentAudioSnapshot{
					AgentID:     msg.AgentID,
					OtherConfig: msg.AgentOtherConfig,
				})
			}

			sendErr := channel.Send(sendCtx, msg)
			msg.Deliver(sendErr)
			if err := sendErr; err != nil {
				slog.Error("error sending message to channel",
					"channel", msg.Channel,
					"chat_id", msg.ChatID,
					"content_len", len(msg.Content),
					"content_preview", Truncate(msg.Content, 160),
					"error", err,
				)
				// Try to send a text-only error notification back to the chat.
				// Only for media failures — text-only failures likely mean the chat
				// is inaccessible (kicked, blocked, etc.) so retrying won't help.
				if len(msg.Media) > 0 {
					notifyMsg := bus.OutboundMessage{
						Channel:  msg.Channel,
						ChatID:   msg.ChatID,
						Content:  formatChannelSendError(err),
						Metadata: sendErrorMeta(msg.Metadata),
						TenantID: msg.TenantID,
					}
					if err2 := channel.Send(sendCtx, notifyMsg); err2 != nil {
						slog.Warn("failed to send error notification",
							"channel", msg.Channel, "error", err2)
					}
				}
			}

			// Clean up temp media files only. Workspace-generated files are preserved
			// so they remain accessible via workspace/web UI after delivery.
			tmpDir := os.TempDir()
			for _, media := range msg.Media {
				if media.URL != "" && strings.HasPrefix(media.URL, tmpDir) {
					if err := os.Remove(media.URL); err != nil {
						slog.Debug("failed to clean up media file", "path", media.URL, "error", err)
					}
				}
			}
		}
	}
}

// WebhookHandlers returns all webhook handlers from channels that implement WebhookChannel.
// Used to mount webhook routes on the main gateway mux.
func (m *Manager) WebhookHandlers() []WebhookRoute {
	m.mu.RLock()
	defer m.mu.RUnlock()

	var routes []WebhookRoute
	for _, ch := range m.channels {
		if wh, ok := ch.(WebhookChannel); ok {
			if path, handler := wh.WebhookHandler(); path != "" && handler != nil {
				routes = append(routes, WebhookRoute{Path: path, Handler: handler})
			}
		}
	}
	return routes
}

// SendToChannel delivers a message to a specific channel by name.
func (m *Manager) SendToChannel(ctx context.Context, channelName, chatID, content string) error {
	m.mu.RLock()
	channel, exists := m.channels[channelName]
	m.mu.RUnlock()

	if !exists {
		return fmt.Errorf("channel %s not found", channelName)
	}

	msg := bus.OutboundMessage{
		Channel: channelName,
		ChatID:  chatID,
		Content: content,
	}

	return channel.Send(ctx, msg)
}

// SendMediaToChannel delivers a message with media attachments to a specific channel by name.
// media must be non-empty; use SendToChannel for text-only messages.
// Returns ErrMediaUnsupported if the channel type does not support media.
func (m *Manager) SendMediaToChannel(ctx context.Context, channelName, chatID, content string, media []bus.MediaAttachment) error {
	if len(media) == 0 {
		return fmt.Errorf("SendMediaToChannel: media slice must not be empty; use SendToChannel for text-only messages")
	}

	m.mu.RLock()
	channel, exists := m.channels[channelName]
	m.mu.RUnlock()

	if !exists {
		return fmt.Errorf("channel %s not found", channelName)
	}

	if !IsMediaCapable(channel.Type()) {
		return fmt.Errorf("%w: %s (%s)", ErrMediaUnsupported, channelName, channel.Type())
	}

	msg := bus.OutboundMessage{
		Channel: channelName,
		ChatID:  chatID,
		Content: content,
		Media:   media,
	}

	return channel.Send(ctx, msg)
}

// DispatchOutbound delivers a full OutboundMessage (with metadata) synchronously.
// Used by message tool action="post" for synchronous error feedback.
func (m *Manager) DispatchOutbound(ctx context.Context, msg bus.OutboundMessage) error {
	m.mu.RLock()
	channel, exists := m.channels[msg.Channel]
	m.mu.RUnlock()

	if !exists {
		return &notSentError{fmt.Errorf("channel %s not found", msg.Channel)}
	}
	if mode := msg.Metadata["fb_mode"]; msg.Metadata != nil && (mode == "feed_post" || mode == "reels_post") {
		if err := checkPagePublisher(channel, msg, mode); err != nil {
			return &notSentError{err}
		}
	}

	return channel.Send(ctx, msg)
}

// notSentError is a dispatch refused before the channel's Send ran. Nothing
// reached the platform, so a caller holding a publish reservation may release it.
type notSentError struct{ err error }

func (e *notSentError) Error() string      { return e.err.Error() }
func (e *notSentError) Unwrap() error      { return e.err }
func (e *notSentError) NotPublished() bool { return true }

// checkPagePublisher decides whether a feed post or reel may go to channel:
// the right destination, an allowed publisher, the same tenant, and an
// approval digest for any media.
func checkPagePublisher(channel Channel, msg bus.OutboundMessage, mode string) error {
	if channel.Type() != TypeFacebook {
		return fmt.Errorf("channel %s is %s, not a facebook feed publisher", msg.Channel, channel.Type())
	}
	if mode == "feed_post" && msg.ChatID != "feed" {
		return fmt.Errorf("facebook feed publisher destination must be feed")
	}
	if mode == "reels_post" && msg.ChatID != "reels" {
		return fmt.Errorf("facebook reels publisher destination must be reels")
	}
	publisherAgentID := strings.TrimSpace(msg.Metadata["publisher_agent_id"])
	owner, ok := channel.(interface{ AgentID() string })
	if !ok || publisherAgentID == "" || strings.TrimSpace(owner.AgentID()) == "" {
		return fmt.Errorf("facebook feed publisher ownership metadata missing")
	}
	if !publisherAllowed(channel, strings.TrimSpace(owner.AgentID()), publisherAgentID) {
		return fmt.Errorf("facebook feed publisher is not owned by calling agent")
	}
	if mode == "reels_post" && (len(msg.Media) != 1 || msg.Metadata["approved_media_sha256"] == "") {
		return fmt.Errorf("facebook reels publisher needs exactly one approved video")
	}
	tenantOwner, ok := channel.(interface{ TenantID() uuid.UUID })
	if !ok || msg.TenantID == uuid.Nil || tenantOwner.TenantID() == uuid.Nil {
		return fmt.Errorf("facebook feed publisher tenant metadata missing")
	}
	if msg.TenantID != tenantOwner.TenantID() {
		return fmt.Errorf("facebook feed publisher tenant mismatch")
	}
	if expectedMediaSHA := msg.Metadata["approved_media_sha256"]; len(msg.Media) == 1 && expectedMediaSHA == "" {
		return fmt.Errorf("facebook feed publisher media approval digest missing")
	}
	return nil
}

// publisherAllowed reports whether agentKey may publish through a page channel:
// the agent the channel instance is bound to, plus any agent the instance's own
// config names as a co-publisher (facebook "publishers").
func publisherAllowed(channel Channel, owner, agentKey string) bool {
	if agentKey == owner {
		return true
	}
	if pub, ok := channel.(interface{ AllowsPublisher(string) bool }); ok {
		return pub.AllowsPublisher(agentKey)
	}
	return false
}

// --- Send error notification helpers ---

// telegramAPIDescRe extracts the human-readable description from Telegram Bot API errors.
// Example: `telego: sendPhoto: api: 400 "Bad Request: not enough rights to send photos to the chat"`
//
//	→ "not enough rights to send photos to the chat"
var telegramAPIDescRe = regexp.MustCompile(`"Bad Request:\s*(.+?)"`)

// formatChannelSendError converts a channel.Send error into a user-friendly message.
// Never exposes raw library/HTTP details.
func formatChannelSendError(err error) string {
	raw := err.Error()
	lower := strings.ToLower(raw)

	// Telegram "Bad Request: <description>" — extract description
	if m := telegramAPIDescRe.FindStringSubmatch(raw); len(m) == 2 {
		return fmt.Sprintf("⚠️ Send failed: %s", m[1])
	}

	// Common Telegram API errors (non-Bad Request)
	switch {
	case strings.Contains(lower, "not enough rights"):
		return "⚠️ Send failed: bot doesn't have permission to send this type of message."
	case strings.Contains(lower, "chat not found"):
		return "⚠️ Send failed: chat not found."
	case strings.Contains(lower, "bot was blocked"):
		return "⚠️ Send failed: bot was blocked by the user."
	case strings.Contains(lower, "user is deactivated"):
		return "⚠️ Send failed: user account is deactivated."
	case strings.Contains(lower, "too many requests") || strings.Contains(lower, "flood"):
		return "⚠️ Send failed: rate limited by Telegram. Please try again later."
	case strings.Contains(lower, "file is too big") || strings.Contains(lower, "wrong file"):
		return "⚠️ Send failed: file is too large or invalid for Telegram."
	}

	// Generic fallback — don't expose internals
	return "⚠️ Failed to deliver message. Check bot logs for details."
}

// sendErrorMeta copies only the routing fields from outbound metadata.
// Strips reply_to_message_id, placeholder_key, audio_as_voice, etc.
// that could cause unintended side effects on the error notification.
func sendErrorMeta(orig map[string]string) map[string]string {
	if orig == nil {
		return nil
	}
	meta := make(map[string]string)
	for _, k := range []string{"local_key", "message_thread_id"} {
		if v := orig[k]; v != "" {
			meta[k] = v
		}
	}
	if len(meta) == 0 {
		return nil
	}
	return meta
}
