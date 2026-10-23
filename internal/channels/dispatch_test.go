package channels

import (
	"context"
	"errors"
	"testing"

	"github.com/google/uuid"

	"github.com/nextlevelbuilder/goclaw/internal/bus"
)

type dispatchTestChannel struct {
	name      string
	kind      string
	agentID   string
	tenantID  uuid.UUID
	sendCalls int
	sendErr   error
}

func (c *dispatchTestChannel) Name() string                    { return c.name }
func (c *dispatchTestChannel) Type() string                    { return c.kind }
func (c *dispatchTestChannel) AgentID() string                 { return c.agentID }
func (c *dispatchTestChannel) TenantID() uuid.UUID             { return c.tenantID }
func (c *dispatchTestChannel) Start(ctx context.Context) error { return nil }
func (c *dispatchTestChannel) Stop(ctx context.Context) error  { return nil }
func (c *dispatchTestChannel) Send(ctx context.Context, msg bus.OutboundMessage) error {
	c.sendCalls++
	return c.sendErr
}
func (c *dispatchTestChannel) IsRunning() bool                { return true }
func (c *dispatchTestChannel) IsAllowed(senderID string) bool { return true }

func TestDispatchOutboundRejectsFeedPostForNonFacebookChannel(t *testing.T) {
	mgr := NewManager(bus.New())
	ch := &dispatchTestChannel{name: "discord-bot", kind: TypeDiscord}
	mgr.RegisterChannel("discord-bot", ch)

	err := mgr.DispatchOutbound(context.Background(), bus.OutboundMessage{
		Channel:  "discord-bot",
		ChatID:   "feed",
		Content:  "public post",
		TenantID: uuid.Nil,
		Metadata: map[string]string{
			"fb_mode":            "feed_post",
			"publisher_agent_id": "zip-crazy",
		},
	})
	if err == nil {
		t.Fatal("expected non-facebook feed_post rejection")
	}
	if ch.sendCalls != 0 {
		t.Fatalf("Send called %d times, want 0", ch.sendCalls)
	}
}

func TestDispatchOutboundAllowsFeedPostForFacebookChannel(t *testing.T) {
	mgr := NewManager(bus.New())
	tenantID := uuid.MustParse("0193a5b0-7000-7000-8000-000000000001")
	ch := &dispatchTestChannel{name: "fb-page", kind: TypeFacebook, agentID: "zip-crazy", tenantID: tenantID}
	mgr.RegisterChannel("fb-page", ch)

	err := mgr.DispatchOutbound(context.Background(), bus.OutboundMessage{
		Channel:  "fb-page",
		ChatID:   "feed",
		Content:  "public post",
		TenantID: tenantID,
		Metadata: map[string]string{
			"fb_mode":            "feed_post",
			"publisher_agent_id": "zip-crazy",
		},
	})
	if err != nil {
		t.Fatalf("DispatchOutbound: %v", err)
	}
	if ch.sendCalls != 1 {
		t.Fatalf("Send called %d times, want 1", ch.sendCalls)
	}
}

func TestDispatchOutboundRejectsFeedPostFromNonOwnerAgent(t *testing.T) {
	mgr := NewManager(bus.New())
	tenantID := uuid.MustParse("0193a5b0-7000-7000-8000-000000000001")
	ch := &dispatchTestChannel{name: "fb-page", kind: TypeFacebook, agentID: "zip-crazy", tenantID: tenantID}
	mgr.RegisterChannel("fb-page", ch)

	err := mgr.DispatchOutbound(context.Background(), bus.OutboundMessage{
		Channel:  "fb-page",
		ChatID:   "feed",
		Content:  "public post",
		TenantID: tenantID,
		Metadata: map[string]string{
			"fb_mode":            "feed_post",
			"publisher_agent_id": "other-agent",
		},
	})
	if err == nil {
		t.Fatal("expected ownership rejection")
	}
	if ch.sendCalls != 0 {
		t.Fatalf("Send called %d times, want 0", ch.sendCalls)
	}
}

type publisherTestChannel struct {
	dispatchTestChannel
	publishers []string
}

func (c *publisherTestChannel) AllowsPublisher(agentKey string) bool {
	for _, p := range c.publishers {
		if p == agentKey {
			return true
		}
	}
	return false
}

func reelsMessage(tenantID uuid.UUID, publisher string) bus.OutboundMessage {
	return bus.OutboundMessage{
		Channel:  "fb-page",
		ChatID:   "reels",
		Content:  "caption",
		TenantID: tenantID,
		Media:    []bus.MediaAttachment{{URL: "/tmp/review.mp4", ContentType: "video/mp4"}},
		Metadata: map[string]string{
			"fb_mode":               "reels_post",
			"publisher_agent_id":    publisher,
			"approved_media_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
		},
	}
}

func TestDispatchOutboundReelsRoute(t *testing.T) {
	tenantID := uuid.MustParse("0193a5b0-7000-7000-8000-000000000001")
	for name, tc := range map[string]struct {
		mutate   func(*bus.OutboundMessage)
		wantSent bool
	}{
		"co-publisher allowed":       {mutate: func(*bus.OutboundMessage) {}, wantSent: true},
		"unlisted agent refused":     {mutate: func(m *bus.OutboundMessage) { m.Metadata["publisher_agent_id"] = "other" }},
		"feed destination refused":   {mutate: func(m *bus.OutboundMessage) { m.ChatID = "feed" }},
		"missing video refused":      {mutate: func(m *bus.OutboundMessage) { m.Media = nil }},
		"missing digest refused":     {mutate: func(m *bus.OutboundMessage) { delete(m.Metadata, "approved_media_sha256") }},
		"feed post to reels refused": {mutate: func(m *bus.OutboundMessage) { m.Metadata["fb_mode"] = "feed_post" }},
	} {
		t.Run(name, func(t *testing.T) {
			mgr := NewManager(bus.New())
			ch := &publisherTestChannel{
				dispatchTestChannel: dispatchTestChannel{name: "fb-page", kind: TypeFacebook, agentID: "cf-director", tenantID: tenantID},
				publishers:          []string{"vf-director"},
			}
			mgr.RegisterChannel("fb-page", ch)
			msg := reelsMessage(tenantID, "vf-director")
			tc.mutate(&msg)
			err := mgr.DispatchOutbound(context.Background(), msg)
			if tc.wantSent != (err == nil) || tc.wantSent != (ch.sendCalls == 1) {
				t.Fatalf("err = %v, sendCalls = %d, want sent = %v", err, ch.sendCalls, tc.wantSent)
			}
			// A refusal happens before Send, so the caller may release its
			// publish reservation instead of locking the approval forever.
			if !tc.wantSent && !isNotPublished(err) {
				t.Fatalf("refusal %v is not marked NotPublished", err)
			}
		})
	}
}

func TestDispatchOutboundPassesSendFailureThroughUnmarked(t *testing.T) {
	tenantID := uuid.MustParse("0193a5b0-7000-7000-8000-000000000001")
	mgr := NewManager(bus.New())
	sendErr := errors.New("finish timed out")
	ch := &publisherTestChannel{
		dispatchTestChannel: dispatchTestChannel{name: "fb-page", kind: TypeFacebook, agentID: "vf-director", tenantID: tenantID, sendErr: sendErr},
	}
	mgr.RegisterChannel("fb-page", ch)
	err := mgr.DispatchOutbound(context.Background(), reelsMessage(tenantID, "vf-director"))
	if !errors.Is(err, sendErr) {
		t.Fatalf("err = %v, want the channel's own error", err)
	}
	// Once Send ran the outcome is ambiguous; only the channel may say otherwise.
	if isNotPublished(err) {
		t.Fatal("a Send failure must not be marked NotPublished by the dispatcher")
	}
}

func isNotPublished(err error) bool {
	var unpublished interface{ NotPublished() bool }
	return errors.As(err, &unpublished) && unpublished.NotPublished()
}
