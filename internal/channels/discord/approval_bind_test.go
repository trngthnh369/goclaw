package discord

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/bwmarrin/discordgo"

	"github.com/nextlevelbuilder/goclaw/internal/bus"
)

type approvalFixture struct {
	history  string            // GET messages?before=
	later    string            // GET messages?after=
	messages map[string]string // GET messages/{id}

	mu    sync.Mutex
	sent  []string
	calls []string
}

func (f *approvalFixture) record(call string) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.calls = append(f.calls, call)
}

func (f *approvalFixture) sentBodies() []string {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]string(nil), f.sent...)
}

func (f *approvalFixture) historyFetched() bool {
	f.mu.Lock()
	defer f.mu.Unlock()
	for _, c := range f.calls {
		if strings.Contains(c, "before=") {
			return true
		}
	}
	return false
}

func (f *approvalFixture) markerAdded() bool {
	f.mu.Lock()
	defer f.mu.Unlock()
	for _, c := range f.calls {
		if strings.HasPrefix(c, "PUT ") && strings.Contains(c, "/reactions/") {
			return true
		}
	}
	return false
}

func newApprovalServer(t *testing.T, f *approvalFixture) *httptest.Server {
	t.Helper()
	server := httptest.NewServer(nil)
	expand := func(s string) string { return strings.ReplaceAll(s, "__SERVER__", server.URL) }
	server.Config.Handler = http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		f.record(r.Method + " " + r.URL.String())
		w.Header().Set("Content-Type", "application/json")
		const msgs = "/channels/thread-1/messages"
		switch {
		case r.Method == http.MethodGet && r.URL.Path == "/channels/thread-1":
			_, _ = fmt.Fprint(w, `{"id":"thread-1","guild_id":"guild-1","type":0}`)
		case r.Method == http.MethodGet && r.URL.Path == msgs && r.URL.Query().Get("before") != "":
			if got := r.URL.Query().Get("limit"); got != "50" {
				t.Errorf("history limit = %q, want 50", got)
			}
			_, _ = fmt.Fprint(w, expand(orEmptyList(f.history)))
		case r.Method == http.MethodGet && r.URL.Path == msgs && r.URL.Query().Get("after") != "":
			_, _ = fmt.Fprint(w, expand(orEmptyList(f.later)))
		case r.Method == http.MethodGet && strings.HasPrefix(r.URL.Path, msgs+"/"):
			id := strings.TrimPrefix(r.URL.Path, msgs+"/")
			m, ok := f.messages[id]
			if !ok {
				http.NotFound(w, r)
				return
			}
			_, _ = fmt.Fprint(w, expand(m))
		case r.Method == http.MethodPost && r.URL.Path == msgs:
			f.mu.Lock()
			f.sent = append(f.sent, string(body))
			f.mu.Unlock()
			_, _ = fmt.Fprint(w, `{"id":"placeholder-1","channel_id":"thread-1","content":"Thinking..."}`)
		case r.Method == http.MethodPost && r.URL.Path == "/channels/thread-1/typing":
			w.WriteHeader(http.StatusNoContent)
		case r.Method == http.MethodPut && strings.Contains(r.URL.Path, "/reactions/"):
			w.WriteHeader(http.StatusNoContent)
		case r.Method == http.MethodGet && strings.HasPrefix(r.URL.Path, "/cdn/"):
			w.Header().Set("Content-Type", "image/png")
			_, _ = fmt.Fprint(w, "png!")
		default:
			t.Errorf("unexpected Discord request: %s %s", r.Method, r.URL.String())
			http.NotFound(w, r)
		}
	})
	return server
}

func orEmptyList(s string) string {
	if s == "" {
		return "[]"
	}
	return s
}

const draftMarkerJSON = `"reactions":[{"count":1,"me":true,"emoji":{"id":null,"name":"📝"}}]`

// draftJSON is a review draft as the bot sends it: text, one image, and the
// bot's own 📝 marker.
func draftJSON(id, content string, age time.Duration) string {
	return fmt.Sprintf(`{"id":%q,"channel_id":"thread-1","content":%q,"timestamp":%q,
		"author":{"id":"bot-1","username":"GoClaw","bot":true},%s,
		"attachments":[{"id":"att-%s","filename":"%s.png","content_type":"image/png","size":4,"url":"__SERVER__/cdn/%s.png"}]}`,
		id, content, time.Now().Add(-age).Format(time.RFC3339), draftMarkerJSON, id, id, id)
}

func textDraftJSON(id, content string) string {
	return fmt.Sprintf(`{"id":%q,"channel_id":"thread-1","content":%q,"timestamp":%q,
		"author":{"id":"bot-1","username":"GoClaw","bot":true},%s}`,
		id, content, time.Now().Format(time.RFC3339), draftMarkerJSON)
}

// unmarkedImageJSON looks like a draft but was never marked: a legacy draft,
// or any other bot message that happens to carry text and an image.
func unmarkedImageJSON(id, content string) string {
	return fmt.Sprintf(`{"id":%q,"channel_id":"thread-1","content":%q,"timestamp":%q,
		"author":{"id":"bot-1","username":"GoClaw","bot":true},
		"reactions":[{"count":1,"me":false,"emoji":{"id":null,"name":"📝"}}],
		"attachments":[{"id":"att-%s","filename":"%s.png","content_type":"image/png","size":4,"url":"__SERVER__/cdn/%s.png"}]}`,
		id, content, time.Now().Format(time.RFC3339), id, id, id)
}

func botTextJSON(id, content string) string {
	return fmt.Sprintf(`{"id":%q,"channel_id":"thread-1","content":%q,"timestamp":%q,
		"author":{"id":"bot-1","username":"GoClaw","bot":true}}`,
		id, content, time.Now().Format(time.RFC3339))
}

func userTextJSON(id, content string) string {
	return fmt.Sprintf(`{"id":%q,"channel_id":"thread-1","content":%q,"timestamp":%q,
		"author":{"id":"current-user","username":"Current"}}`,
		id, content, time.Now().Format(time.RFC3339))
}

func jsonList(items ...string) string { return "[" + strings.Join(items, ",") + "]" }

// plainGroupMessage is a message typed in the channel without a reply or an
// @mention — the case require_mention used to drop.
func plainGroupMessage(content string) *discordgo.MessageCreate {
	return &discordgo.MessageCreate{Message: &discordgo.Message{
		ID:        "current-1",
		ChannelID: "thread-1",
		GuildID:   "guild-1",
		Content:   content,
		Author:    &discordgo.User{ID: "current-user", Username: "Current"},
		Timestamp: time.Now(),
	}}
}

func expectNoInbound(t *testing.T, mb *bus.MessageBus) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 150*time.Millisecond)
	defer cancel()
	if msg, ok := mb.ConsumeInbound(ctx); ok {
		t.Fatalf("unexpected inbound message: %+v", msg.Metadata)
	}
}

func TestImplicitApprovalBindsSinglePendingDraft(t *testing.T) {
	f := &approvalFixture{history: jsonList(
		userTextJSON("u1", "hay đấy"),
		draftJSON("d1", "Article A", time.Hour),
	)}
	server := newApprovalServer(t, f)
	defer server.Close()
	ch, mb := newThreadBackfillTestChannel(t, server)

	ch.handleMessage(ch.session, plainGroupMessage("Duyệt nhé em"))

	inbound := consumeThreadBackfillInbound(t, mb)
	want := map[string]string{
		"message_id":              "current-1",
		"current_message":         "Duyệt nhé em",
		"reply_to_message_id":     "d1",
		"reply_to_content":        "Article A",
		"reply_to_author_id":      "bot-1",
		"reply_to_media_count":    "1",
		"reply_to_media_complete": "true",
		"approval_sender_allowed": "true",
	}
	for k, v := range want {
		if got := inbound.Metadata[k]; got != v {
			t.Errorf("metadata[%q] = %q, want %q", k, got, v)
		}
	}
	if !strings.HasPrefix(inbound.Metadata["reply_to_media"], "d1.png=") {
		t.Errorf("reply_to_media = %q, want d1.png digest", inbound.Metadata["reply_to_media"])
	}
}

func TestImplicitApprovalBindsTextOnlyDraft(t *testing.T) {
	f := &approvalFixture{history: jsonList(textDraftJSON("d1", "Article without image"))}
	server := newApprovalServer(t, f)
	defer server.Close()
	ch, mb := newThreadBackfillTestChannel(t, server)

	ch.handleMessage(ch.session, plainGroupMessage("duyệt"))

	inbound := consumeThreadBackfillInbound(t, mb)
	if got := inbound.Metadata["reply_to_message_id"]; got != "d1" {
		t.Fatalf("reply_to_message_id = %q, want d1", got)
	}
	if got := inbound.Metadata["reply_to_media_complete"]; got != "true" {
		t.Fatalf("reply_to_media_complete = %q, want true", got)
	}
}

// Only the bot's own marker makes a message a draft. A long bot answer, a
// legacy draft, or a 📝 someone else added must never be approved implicitly.
func TestImplicitApprovalIgnoresUnmarkedMessages(t *testing.T) {
	f := &approvalFixture{history: jsonList(
		botTextJSON("chat", "Một câu trả lời dài của bot, không phải bài nháp."),
		unmarkedImageJSON("legacy", "Legacy draft"),
	)}
	server := newApprovalServer(t, f)
	defer server.Close()
	ch, mb := newThreadBackfillTestChannel(t, server)

	ch.handleMessage(ch.session, plainGroupMessage("duyệt"))

	expectNoInbound(t, mb)
}

func TestReviewDraftSendAddsMarker(t *testing.T) {
	for name, tc := range map[string]struct {
		meta       map[string]string
		wantMarker bool
	}{
		"review draft": {meta: map[string]string{"cf_review_draft": "true"}, wantMarker: true},
		"plain send":   {meta: nil, wantMarker: false},
	} {
		t.Run(name, func(t *testing.T) {
			f := &approvalFixture{}
			server := newApprovalServer(t, f)
			defer server.Close()
			ch, _ := newThreadBackfillTestChannel(t, server)

			if err := ch.Send(context.Background(), bus.OutboundMessage{
				Channel: "discord", ChatID: "thread-1", Content: "Article", Metadata: tc.meta,
			}); err != nil {
				t.Fatalf("Send: %v", err)
			}
			if got := f.markerAdded(); got != tc.wantMarker {
				t.Fatalf("marker added = %v, want %v (calls %v)", got, tc.wantMarker, f.calls)
			}
		})
	}
}

func TestImplicitApprovalAsksWhenSeveralDraftsPending(t *testing.T) {
	f := &approvalFixture{history: jsonList(
		draftJSON("d2", "Article B", time.Minute),
		draftJSON("d1", "Article A", time.Hour),
	)}
	server := newApprovalServer(t, f)
	defer server.Close()
	ch, mb := newThreadBackfillTestChannel(t, server)

	ch.handleMessage(ch.session, plainGroupMessage("duyệt"))

	expectNoInbound(t, mb)
	sent := f.sentBodies()
	if len(sent) != 1 || !strings.Contains(sent[0], "2 bài nháp") {
		t.Fatalf("sent = %q, want one ambiguity notice", sent)
	}
}

func TestImplicitApprovalSkipsDraftNamedByPublishNotice(t *testing.T) {
	f := &approvalFixture{history: jsonList(
		botTextJSON("n1", "✅ Đã đăng lên fanpage.\npost_id: 1_2\ndraft_id: 222"),
		draftJSON("222", "Article B", time.Minute),
		draftJSON("111", "Article A", time.Hour),
	)}
	server := newApprovalServer(t, f)
	defer server.Close()
	ch, mb := newThreadBackfillTestChannel(t, server)

	ch.handleMessage(ch.session, plainGroupMessage("duyệt"))

	inbound := consumeThreadBackfillInbound(t, mb)
	if got := inbound.Metadata["reply_to_message_id"]; got != "111" {
		t.Fatalf("reply_to_message_id = %q, want the unpublished draft 111", got)
	}
}

func TestImplicitApprovalLegacyNoticeHidesOlderDrafts(t *testing.T) {
	f := &approvalFixture{history: jsonList(
		botTextJSON("n1", "✅ Đã đăng lên fanpage.\npost_id: 1_2"),
		draftJSON("d1", "Article A", time.Hour),
	)}
	server := newApprovalServer(t, f)
	defer server.Close()
	ch, mb := newThreadBackfillTestChannel(t, server)

	ch.handleMessage(ch.session, plainGroupMessage("duyệt"))

	expectNoInbound(t, mb)
}

func TestImplicitApprovalIgnoresStaleAndNonDraftMessages(t *testing.T) {
	f := &approvalFixture{history: jsonList(
		botTextJSON("abort", "Pipeline dừng: không tìm được nguồn"),
		draftJSON("old", "Old article", 100*time.Hour),
	)}
	server := newApprovalServer(t, f)
	defer server.Close()
	ch, mb := newThreadBackfillTestChannel(t, server)

	ch.handleMessage(ch.session, plainGroupMessage("duyệt"))

	expectNoInbound(t, mb)
}

func TestImplicitApprovalRequiresApprovalAuthority(t *testing.T) {
	f := &approvalFixture{history: jsonList(draftJSON("d1", "Article A", time.Hour))}
	server := newApprovalServer(t, f)
	defer server.Close()
	ch, mb := newThreadBackfillTestChannel(t, server)
	ch.config.ApprovalAllowFrom = nil

	ch.handleMessage(ch.session, plainGroupMessage("duyệt"))

	expectNoInbound(t, mb)
	if f.historyFetched() {
		t.Fatal("history must not be read for a sender without approval authority")
	}
}

func TestImplicitApprovalIgnoresNonApprovalText(t *testing.T) {
	f := &approvalFixture{history: jsonList(draftJSON("d1", "Article A", time.Hour))}
	server := newApprovalServer(t, f)
	defer server.Close()
	ch, mb := newThreadBackfillTestChannel(t, server)

	ch.handleMessage(ch.session, plainGroupMessage("đăng bài mới về Nvidia"))

	expectNoInbound(t, mb)
	if f.historyFetched() {
		t.Fatal("history must not be read for a non-approval message")
	}
}

func approvalReaction(emoji, userID, messageID string) *discordgo.MessageReactionAdd {
	return &discordgo.MessageReactionAdd{
		MessageReaction: &discordgo.MessageReaction{
			UserID:    userID,
			MessageID: messageID,
			ChannelID: "thread-1",
			GuildID:   "guild-1",
			Emoji:     discordgo.Emoji{Name: emoji},
		},
		Member: &discordgo.Member{User: &discordgo.User{ID: userID, Username: "Current"}},
	}
}

func TestReactionApprovalBindsReactedDraft(t *testing.T) {
	f := &approvalFixture{messages: map[string]string{"d1": draftJSON("d1", "Article A", time.Hour)}}
	server := newApprovalServer(t, f)
	defer server.Close()
	ch, mb := newThreadBackfillTestChannel(t, server)

	ch.handleReactionAdd(ch.session, approvalReaction("✅", "current-user", "d1"))

	inbound := consumeThreadBackfillInbound(t, mb)
	want := map[string]string{
		"message_id":              "d1",
		"current_message":         approvalReactionCommand,
		"reply_to_message_id":     "d1",
		"reply_to_content":        "Article A",
		"reply_to_author_id":      "bot-1",
		"reply_to_media_complete": "true",
		"approval_sender_allowed": "true",
	}
	for k, v := range want {
		if got := inbound.Metadata[k]; got != v {
			t.Errorf("metadata[%q] = %q, want %q", k, got, v)
		}
	}
	if inbound.SenderID != "current-user" {
		t.Errorf("sender = %q, want the reacting approver", inbound.SenderID)
	}
}

func TestReactionApprovalIgnored(t *testing.T) {
	cases := map[string]struct {
		reaction *discordgo.MessageReactionAdd
		messages map[string]string
		later    string
	}{
		"other emoji": {
			reaction: approvalReaction("❤️", "current-user", "d1"),
		},
		"non approver": {
			reaction: approvalReaction("✅", "someone-else", "d1"),
		},
		"unmarked bot message": {
			reaction: approvalReaction("✅", "current-user", "t1"),
			messages: map[string]string{"t1": botTextJSON("t1", "just text")},
		},
		"legacy draft": {
			reaction: approvalReaction("✅", "current-user", "l1"),
			messages: map[string]string{"l1": unmarkedImageJSON("l1", "Legacy draft")},
		},
		"thumbs up is not approval": {
			reaction: approvalReaction("👍", "current-user", "d1"),
		},
		"user message": {
			reaction: approvalReaction("✅", "current-user", "u1"),
			messages: map[string]string{"u1": userTextJSON("u1", "my note")},
		},
		"already published": {
			reaction: approvalReaction("✅", "current-user", "555"),
			messages: map[string]string{"555": draftJSON("555", "Article A", time.Hour)},
			later:    jsonList(botTextJSON("n1", "✅ Đã đăng lên fanpage.\npost_id: 1_2\ndraft_id: 555")),
		},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			msgs := tc.messages
			if msgs == nil {
				msgs = map[string]string{"d1": draftJSON("d1", "Article A", time.Hour)}
			}
			f := &approvalFixture{messages: msgs, later: tc.later}
			server := newApprovalServer(t, f)
			defer server.Close()
			ch, mb := newThreadBackfillTestChannel(t, server)

			ch.handleReactionAdd(ch.session, tc.reaction)

			expectNoInbound(t, mb)
		})
	}
}

// videoDraftJSON is a Video Factory review draft: text, one video, and the
// bot's own 📝 marker.
func videoDraftJSON(id, content string) string {
	return fmt.Sprintf(`{"id":%q,"channel_id":"thread-1","content":%q,"timestamp":%q,
		"author":{"id":"bot-1","username":"GoClaw","bot":true},%s,
		"attachments":[{"id":"att-%s","filename":"%s.mp4","content_type":"video/mp4","size":4,"url":"__SERVER__/cdn/%s.mp4"}]}`,
		id, content, time.Now().Format(time.RFC3339), draftMarkerJSON, id, id, id)
}

// A video draft is approvable only in a channel the instance names as a Reels
// review channel, and the approval then carries that publish target.
func TestImplicitApprovalBindsVideoDraftInReelsReviewChannel(t *testing.T) {
	f := &approvalFixture{history: jsonList(videoDraftJSON("v1", "Video A\n[caption]\nChú thích\n[/caption]"))}
	server := newApprovalServer(t, f)
	defer server.Close()
	ch, mb := newThreadBackfillTestChannel(t, server)
	ch.config.ReelsReviewChatIDs = []string{"thread-1"}

	ch.handleMessage(ch.session, plainGroupMessage("duyệt"))

	inbound := consumeThreadBackfillInbound(t, mb)
	if got := inbound.Metadata["reply_to_message_id"]; got != "v1" {
		t.Fatalf("reply_to_message_id = %q, want v1", got)
	}
	if got := inbound.Metadata["approval_publish_target"]; got != "reels" {
		t.Fatalf("approval_publish_target = %q, want reels", got)
	}
	if !strings.HasPrefix(inbound.Metadata["reply_to_media"], "v1.mp4=") {
		t.Fatalf("reply_to_media = %q, want the video digest", inbound.Metadata["reply_to_media"])
	}
}

func TestImplicitApprovalIgnoresVideoDraftOutsideReelsReviewChannel(t *testing.T) {
	f := &approvalFixture{history: jsonList(videoDraftJSON("v1", "Video A\n[caption]\nChú thích\n[/caption]"))}
	server := newApprovalServer(t, f)
	defer server.Close()
	ch, mb := newThreadBackfillTestChannel(t, server)

	ch.handleMessage(ch.session, plainGroupMessage("duyệt"))

	expectNoInbound(t, mb)
}

func TestApprovalPublishTargetOnlyForConfiguredChats(t *testing.T) {
	ch := &Channel{}
	ch.config.ReelsReviewChatIDs = []string{" reels-chat "}
	if got := ch.approvalPublishTarget("reels-chat"); got != "reels" {
		t.Fatalf("configured chat target = %q, want reels", got)
	}
	for _, chat := range []string{"", "other-chat", "reels"} {
		if got := ch.approvalPublishTarget(chat); got != "" {
			t.Fatalf("approvalPublishTarget(%q) = %q, want empty", chat, got)
		}
	}
}
