package tools

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/google/uuid"

	"github.com/nextlevelbuilder/goclaw/internal/bus"
	"github.com/nextlevelbuilder/goclaw/internal/store"
)

const (
	reelsReviewChat = "vf-review-chat"
	reelsCaptionTxt = "3 mẹo Google Maps cho chuyến đi tới.\n\n#dulich #meohay"
	reelsReviewText = "🎬 Video Factory · 3 mẹo Google Maps\njob: vf-x\n\n[caption]\n" + reelsCaptionTxt +
		"\n[/caption]\n\nTrả lời \"duyệt\" để đăng Reels."
)

// reelsFixture is an approved Reels review: the video the Discord handler
// persisted from the reviewed message, and the run context it built.
type reelsFixture struct {
	tool       *MessageTool
	video      string
	videoSHA   string
	dispatched []bus.OutboundMessage
	videoBytes []byte
	err        error
}

func newReelsFixture(t *testing.T) *reelsFixture {
	t.Helper()
	workspace := t.TempDir()
	video := filepath.Join(workspace, ".uploads", "preview-1234.mp4")
	if err := os.MkdirAll(filepath.Dir(video), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(video, []byte("approved-mp4-bytes"), 0o600); err != nil {
		t.Fatal(err)
	}
	canonical, err := filepath.EvalSymlinks(video)
	if err != nil {
		t.Fatal(err)
	}
	sha, err := hashFeedPostFile(canonical)
	if err != nil {
		t.Fatal(err)
	}
	f := &reelsFixture{tool: NewMessageTool(workspace, true), video: canonical, videoSHA: sha}
	f.tool.SetDataDir(t.TempDir())
	f.tool.SetOutboundDispatcher(func(_ context.Context, msg bus.OutboundMessage) error {
		f.dispatched = append(f.dispatched, msg)
		if len(msg.Media) == 1 {
			f.videoBytes, _ = os.ReadFile(msg.Media[0].URL)
		}
		return f.err
	})
	return f
}

func (f *reelsFixture) ctx(chatID, publishTarget, reviewed string) context.Context {
	ctx := context.Background()
	ctx = WithToolSessionKey(ctx, "agent:vf-director:discord:group:"+chatID)
	ctx = WithToolChannel(ctx, "vf-discord")
	ctx = WithToolChatID(ctx, chatID)
	ctx = WithToolPeerKind(ctx, "group")
	return store.WithRunContext(ctx, &store.RunContext{
		AgentID:               uuid.MustParse("01a0d1c6-612f-7367-b66f-b610a9ca4d71"),
		AgentKey:              "vf-director",
		TenantID:              uuid.MustParse("0193a5b0-7000-7000-8000-000000000001"),
		SenderID:              "approver-1",
		ChannelType:           "discord",
		CurrentMessage:        "duyệt",
		ReplyToMessageID:      "review-msg-1",
		ReplyToContent:        reviewed,
		ReplyToMedia:          "preview.mp4=" + f.videoSHA,
		ReplyToMediaPaths:     []string{f.video},
		ReplyToMediaCount:     1,
		ReplyToMediaComplete:  true,
		ReplyToAuthorID:       "vf-bot",
		ChannelBotUserID:      "vf-bot",
		ApprovalSenderAllowed: true,
		ApprovalPublishTarget: publishTarget,
	})
}

func (f *reelsFixture) post(ctx context.Context, message string) *Result {
	return f.tool.Execute(ctx, map[string]any{
		"action":         "post",
		"channel":        "fb-page",
		"target":         "reels",
		"message":        message,
		"forward":        true,
		"forward_reason": "duyệt",
	})
}

func TestReelsPost_PublishesApprovedVideoAndCaption(t *testing.T) {
	f := newReelsFixture(t)
	res := f.post(f.ctx(reelsReviewChat, "reels", reelsReviewText), approvedReplyPayloadToken)
	if res == nil || res.IsError {
		t.Fatalf("expected the reel to be published, got %+v", res)
	}
	if len(f.dispatched) != 1 {
		t.Fatalf("dispatched %d messages, want 1", len(f.dispatched))
	}
	msg := f.dispatched[0]
	if msg.Channel != "fb-page" || msg.ChatID != "reels" || msg.Metadata["fb_mode"] != "reels_post" {
		t.Fatalf("destination = %s/%s mode %q", msg.Channel, msg.ChatID, msg.Metadata["fb_mode"])
	}
	if msg.Content != reelsCaptionTxt {
		t.Fatalf("caption = %q, want exactly the reviewed caption block", msg.Content)
	}
	if string(f.videoBytes) != "approved-mp4-bytes" || msg.Metadata["approved_media_sha256"] != f.videoSHA {
		t.Fatalf("video = %q digest %q, want the reviewed attachment", f.videoBytes, msg.Metadata["approved_media_sha256"])
	}
	if msg.Metadata["publisher_agent_id"] != "vf-director" || msg.Metadata["review_message_id"] != "review-msg-1" {
		t.Fatalf("metadata = %+v", msg.Metadata)
	}
}

func TestReelsPost_Refusals(t *testing.T) {
	for name, tc := range map[string]struct {
		chat, target, reviewed, message string
		mutate                          func(*store.RunContext)
		want                            string
	}{
		"chat not configured for reels": {chat: reelsReviewChat, target: "", reviewed: reelsReviewText,
			message: approvedReplyPayloadToken, want: "Reels review channel"},
		"contentfactory channel": {chat: contentFactoryApprovalChannelID, target: "reels", reviewed: reelsReviewText,
			message: approvedReplyPayloadToken, want: "feed posts only"},
		"model retyped the caption": {chat: reelsReviewChat, target: "reels", reviewed: reelsReviewText,
			message: reelsCaptionTxt, want: "APPROVED_REPLY"},
		"no caption block": {chat: reelsReviewChat, target: "reels", reviewed: "Video A, no caption",
			message: approvedReplyPayloadToken, want: "not a publishable Reels draft"},
		"two caption blocks": {chat: reelsReviewChat, target: "reels",
			reviewed: reelsReviewText + "\n[caption]\nx\n[/caption]", message: approvedReplyPayloadToken, want: "more than one"},
		"not an approval command": {chat: reelsReviewChat, target: "reels", reviewed: reelsReviewText,
			message: approvedReplyPayloadToken, mutate: func(rc *store.RunContext) { rc.CurrentMessage = "chưa, sửa lại" },
			want: "positive approval"},
		"sender not allowlisted": {chat: reelsReviewChat, target: "reels", reviewed: reelsReviewText,
			message: approvedReplyPayloadToken, mutate: func(rc *store.RunContext) { rc.ApprovalSenderAllowed = false },
			want: "allowlisted"},
		"no video attached": {chat: reelsReviewChat, target: "reels", reviewed: reelsReviewText,
			message: approvedReplyPayloadToken, mutate: func(rc *store.RunContext) {
				rc.ReplyToMedia, rc.ReplyToMediaCount, rc.ReplyToMediaPaths = "", 0, nil
			}, want: "exactly one video"},
	} {
		t.Run(name, func(t *testing.T) {
			f := newReelsFixture(t)
			ctx := f.ctx(tc.chat, tc.target, tc.reviewed)
			if tc.mutate != nil {
				tc.mutate(store.RunContextFromCtx(ctx))
			}
			res := f.post(ctx, tc.message)
			if res == nil || !res.IsError || !strings.Contains(res.ForLLM, tc.want) {
				t.Fatalf("result = %+v, want an error mentioning %q", res, tc.want)
			}
			if len(f.dispatched) != 0 {
				t.Fatalf("dispatched %d messages, want none", len(f.dispatched))
			}
		})
	}
}

func TestReelsPost_RejectsImageAttachment(t *testing.T) {
	f := newReelsFixture(t)
	image := filepath.Join(filepath.Dir(f.video), "cover.png")
	if err := os.WriteFile(image, []byte("png"), 0o600); err != nil {
		t.Fatal(err)
	}
	sha, _ := hashFeedPostFile(image)
	ctx := f.ctx(reelsReviewChat, "reels", reelsReviewText)
	rc := store.RunContextFromCtx(ctx)
	rc.ReplyToMedia, rc.ReplyToMediaPaths = "cover.png="+sha, []string{image}
	if res := f.post(ctx, approvedReplyPayloadToken); res == nil || !res.IsError {
		t.Fatalf("an image must never be published as a reel, got %+v", res)
	}
	if len(f.dispatched) != 0 {
		t.Fatalf("dispatched %d messages, want none", len(f.dispatched))
	}
}

type fakeUnpublished struct{ notPublished bool }

func (e fakeUnpublished) Error() string      { return "upload failed" }
func (e fakeUnpublished) NotPublished() bool { return e.notPublished }

// A failure before Facebook was asked to publish releases the approval, so the
// same draft can be approved again. An ambiguous failure keeps it reserved.
func TestReelsPost_FailureKinds(t *testing.T) {
	for name, tc := range map[string]struct {
		err        error
		wantSecond int
	}{
		"not published releases the approval": {err: fakeUnpublished{notPublished: true}, wantSecond: 2},
		"ambiguous failure blocks a retry":    {err: errors.New("finish timed out"), wantSecond: 1},
	} {
		t.Run(name, func(t *testing.T) {
			f := newReelsFixture(t)
			f.err = tc.err
			ctx := f.ctx(reelsReviewChat, "reels", reelsReviewText)
			if res := f.post(ctx, approvedReplyPayloadToken); res == nil || !res.IsError {
				t.Fatalf("first attempt: want an error, got %+v", res)
			}
			f.err = nil
			f.post(ctx, approvedReplyPayloadToken)
			if len(f.dispatched) != tc.wantSecond {
				t.Fatalf("dispatch calls after retry = %d, want %d", len(f.dispatched), tc.wantSecond)
			}
		})
	}
}

// A reel approval cannot be spent on a feed post, and the feed route is unchanged.
func TestReelsReviewChatCannotPublishFeedPosts(t *testing.T) {
	f := newReelsFixture(t)
	res := f.tool.Execute(f.ctx(reelsReviewChat, "reels", reelsReviewText), map[string]any{
		"action": "post", "channel": "fb-page", "target": "feed", "message": approvedReplyPayloadToken,
		"forward": true, "forward_reason": "duyệt",
	})
	if res == nil || !res.IsError || !strings.Contains(res.ForLLM, "ContentFactory review channel") {
		t.Fatalf("result = %+v, want the feed route to refuse a reels review chat", res)
	}
}

func TestReelsReviewDraftMarking(t *testing.T) {
	video := []bus.MediaAttachment{{URL: "/tmp/p.mp4", ContentType: "video/mp4"}}
	image := []bus.MediaAttachment{{URL: "/tmp/p.png", ContentType: "image/png"}}
	if !isReelsReviewDraft(reelsReviewText, video) {
		t.Fatal("a video with a caption block is a reels review draft")
	}
	if isReelsReviewDraft(reelsReviewText, image) || isReelsReviewDraft("no caption", video) ||
		isReelsReviewDraft(reelsReviewText, append(video, video...)) {
		t.Fatal("only one video plus a caption block is a reels review draft")
	}
}
