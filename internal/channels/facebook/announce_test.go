package facebook

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/nextlevelbuilder/goclaw/internal/bus"
	"github.com/nextlevelbuilder/goclaw/internal/channels"
)

func feedMsg(meta map[string]string) bus.OutboundMessage {
	return bus.OutboundMessage{Channel: "fb-page", ChatID: "feed", Metadata: meta}
}

// announceChannel builds a Channel wired to b, serving handler as Graph.
func announceChannel(t *testing.T, b *bus.MessageBus, handler http.HandlerFunc) *Channel {
	t.Helper()
	srv := httptest.NewServer(handler)
	t.Cleanup(srv.Close)
	swapGraphBase(t, srv.URL)
	return &Channel{
		BaseChannel: channels.NewBaseChannel("fb-page", b, nil),
		graphClient: NewGraphClient(fixtureToken, "1193343723865442"),
	}
}

// nextOutbound reads one published message, or reports none arrived.
func nextOutbound(t *testing.T, b *bus.MessageBus) (bus.OutboundMessage, bool) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	return b.SubscribeOutbound(ctx)
}

// A publish triggered outside a chat (cron/wake) has nowhere to announce to, so
// it must stay silent rather than invent a destination.
func TestAnnouncePublished_SilentWithoutNotifyTarget(t *testing.T) {
	b := bus.New()
	ch := announceChannel(t, b, func(w http.ResponseWriter, r *http.Request) {
		t.Error("Graph must not be called when there is nowhere to announce to")
	})

	ch.announcePublished(feedMsg(nil), "1_2", "")

	if msg, ok := nextOutbound(t, b); ok {
		t.Fatalf("published %+v, want nothing", msg)
	}
}

func TestAnnouncePublished_IncludesPostIDAndPermalink(t *testing.T) {
	b := bus.New()
	ch := announceChannel(t, b, func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"permalink_url":"https://www.facebook.com/999/posts/123"}`))
	})
	meta := map[string]string{"notify_channel": "cf-discord", "notify_chat": "1530127001602625677"}

	ch.announcePublished(feedMsg(meta), "1193343723865442_122104618611290721", "https://www.facebook.com/999/posts/123")

	m, ok := nextOutbound(t, b)
	if !ok {
		t.Fatal("no announcement published")
	}
	if m.Channel != "cf-discord" || m.ChatID != "1530127001602625677" {
		t.Errorf("routed to %s/%s, want cf-discord/1530127001602625677", m.Channel, m.ChatID)
	}
	// The operator needs the id to delete the post and the link to see it.
	if !strings.Contains(m.Content, "1193343723865442_122104618611290721") {
		t.Errorf("content missing post id: %q", m.Content)
	}
	// The permalink must be the one Graph reported, not one assembled from the
	// configured page id — those differ, and an assembled link 404s.
	if !strings.Contains(m.Content, "https://www.facebook.com/999/posts/123") {
		t.Errorf("content missing permalink: %q", m.Content)
	}
	// Review channels are groups; without group_id the adapter may route it as a DM.
	if m.Metadata["group_id"] != "1530127001602625677" {
		t.Errorf("group_id = %q, want the notify chat", m.Metadata["group_id"])
	}
}

// A permalink lookup failure must not swallow the announcement: the post really
// was published, and the id alone is enough to find and remove it.
func TestAnnouncePublished_StillAnnouncesWhenPermalinkFails(t *testing.T) {
	b := bus.New()
	ch := announceChannel(t, b, func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusBadRequest)
		_, _ = w.Write([]byte(`{"error":{"code":100,"message":"nope"}}`))
	})
	meta := map[string]string{"notify_channel": "cf-discord", "notify_chat": "chat-1"}

	ch.announcePublished(feedMsg(meta), "1_2", "")

	m, ok := nextOutbound(t, b)
	if !ok {
		t.Fatal("no announcement published; a failed permalink lookup must not suppress it")
	}
	if !strings.Contains(m.Content, "1_2") {
		t.Errorf("content missing post id: %q", m.Content)
	}
}
