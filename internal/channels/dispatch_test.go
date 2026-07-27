package channels

import (
	"context"
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
}

func (c *dispatchTestChannel) Name() string                    { return c.name }
func (c *dispatchTestChannel) Type() string                    { return c.kind }
func (c *dispatchTestChannel) AgentID() string                 { return c.agentID }
func (c *dispatchTestChannel) TenantID() uuid.UUID             { return c.tenantID }
func (c *dispatchTestChannel) Start(ctx context.Context) error { return nil }
func (c *dispatchTestChannel) Stop(ctx context.Context) error  { return nil }
func (c *dispatchTestChannel) Send(ctx context.Context, msg bus.OutboundMessage) error {
	c.sendCalls++
	return nil
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
