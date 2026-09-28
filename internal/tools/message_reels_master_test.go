//go:build linux

package tools

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/nextlevelbuilder/goclaw/internal/bus"
	"github.com/nextlevelbuilder/goclaw/internal/store"
)

const (
	masterReviewChat = "vf-review-chat"
	fakeProbeOK      = `{"streams":[{"codec_type":"video","codec_name":"h264","width":1080,"height":1920,"avg_frame_rate":"30/1"},` +
		`{"codec_type":"audio","codec_name":"aac"}],"format":{"duration":"45.7"}}`
)

// masterFixture is a director workspace holding a staged master, fake media
// tools (shell builtins only: the tools run with an empty environment), a data
// dir for drafts and a bus that records what was sent.
type masterFixture struct {
	tool      *MessageTool
	workspace string
	master    string
	bus       *bus.MessageBus
	tenant    uuid.UUID
	review    []byte // the last sent review cut, as Discord would serve it
}

func writeFakeTool(t *testing.T, dir, name, body string) string {
	t.Helper()
	path := filepath.Join(dir, name)
	if err := os.WriteFile(path, []byte("#!/bin/sh\n"+body+"\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	return path
}

// fakeMediaTools installs an ffprobe that prints probeJSON and an ffmpeg that
// writes "review <nonce comment>" to its last argument.
func fakeMediaTools(t *testing.T, probeJSON, ffmpegBody string) {
	t.Helper()
	dir := t.TempDir()
	probe := writeFakeTool(t, dir, "ffprobe", "printf '%s' '"+probeJSON+"'")
	if ffmpegBody == "" {
		ffmpegBody = `for a; do last=$a; case $a in comment=*) c=$a;; esac; done; printf 'review %s\n' "$c" > "$last"`
	}
	ffmpeg := writeFakeTool(t, dir, "ffmpeg", ffmpegBody)
	old, oldDumpable := mediaTools, gatewayNonDumpable
	mediaTools = func() (string, string, error) { return ffmpeg, probe, nil }
	gatewayNonDumpable = func() bool { return true }
	t.Cleanup(func() { mediaTools, gatewayNonDumpable = old, oldDumpable })
}

func newMasterFixture(t *testing.T) *masterFixture {
	t.Helper()
	fakeMediaTools(t, fakeProbeOK, "")
	ws := t.TempDir()
	master := filepath.Join(ws, "vf-staging", "vf-job", "master.mp4")
	if err := os.MkdirAll(filepath.Dir(master), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(master, []byte("\x00\x00\x00\x20ftypisom-master-bytes"), 0o644); err != nil {
		t.Fatal(err)
	}
	f := &masterFixture{tool: NewMessageTool(ws, true), workspace: ws, master: master, bus: bus.New(),
		tenant: uuid.MustParse("0193a5b0-7000-7000-8000-000000000001")}
	f.tool.SetDataDir(t.TempDir())
	f.tool.SetMessageBus(f.bus)
	f.tool.SetReelsReviewChatChecker(func(channel, chatID string) bool {
		return channel == "vf-discord" && chatID == masterReviewChat
	})
	return f
}

func (f *masterFixture) sendCtx() context.Context {
	ctx := WithToolWorkspace(context.Background(), f.workspace)
	ctx = WithToolAgentKey(ctx, "vf-director")
	ctx = WithToolSessionKey(ctx, "agent:vf-director:cron:vf-daily")
	return store.WithRunContext(ctx, &store.RunContext{AgentKey: "vf-director", TenantID: f.tenant})
}

func (f *masterFixture) send(ctx context.Context, message, master string) *Result {
	return f.tool.Execute(ctx, map[string]any{
		"action": "send", "channel": "vf-discord", "target": masterReviewChat,
		"message": message, "reels_master": master,
	})
}

func (f *masterFixture) sent(t *testing.T) bus.OutboundMessage {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	msg, ok := f.bus.SubscribeOutbound(ctx)
	if !ok {
		t.Fatal("nothing was published to the bus")
	}
	if len(msg.Media) == 1 {
		f.review, _ = os.ReadFile(msg.Media[0].URL)
	}
	return msg
}

func (f *masterFixture) draftsRoot() string {
	return filepath.Join(f.tool.dataDir, filepath.FromSlash(reelsDraftsDir), f.tenant.String())
}

func (f *masterFixture) draftDirs(t *testing.T) []string {
	t.Helper()
	entries, _ := os.ReadDir(f.draftsRoot())
	var dirs []string
	for _, e := range entries {
		if e.IsDir() && e.Name() != reelsDraftsIndexDir {
			dirs = append(dirs, e.Name())
		}
	}
	return dirs
}

// approve replays the sent draft as the approved reply: its text and a local
// copy of the attached review video, as the Discord handler would persist it.
func (f *masterFixture) approve(t *testing.T, msg bus.OutboundMessage, mutate func(*store.RunContext)) (*Result, []bus.OutboundMessage) {
	t.Helper()
	review, err := os.ReadFile(msg.Media[0].URL)
	if err != nil {
		review = f.review // the draft dir is gone; Discord still serves what it received
	}
	download := filepath.Join(f.workspace, ".uploads", "review-dl.mp4")
	_ = os.MkdirAll(filepath.Dir(download), 0o755)
	if err := os.WriteFile(download, review, 0o600); err != nil {
		t.Fatal(err)
	}
	reviewSHA, _ := hashFeedPostFile(download)
	var dispatched []bus.OutboundMessage
	f.tool.SetOutboundDispatcher(func(_ context.Context, out bus.OutboundMessage) error {
		dispatched = append(dispatched, out)
		return nil
	})
	ctx := WithToolSessionKey(context.Background(), "agent:vf-director:discord:group:"+masterReviewChat)
	ctx = WithToolChannel(ctx, "vf-discord")
	ctx = WithToolChatID(ctx, masterReviewChat)
	ctx = WithToolPeerKind(ctx, "group")
	ctx = WithToolWorkspace(ctx, f.workspace)
	rc := &store.RunContext{
		AgentID: uuid.MustParse("01a0d1c6-612f-7367-b66f-b610a9ca4d71"), AgentKey: "vf-director", TenantID: f.tenant,
		SenderID: "approver-1", ChannelType: "discord", CurrentMessage: "duyệt", ReplyToMessageID: "review-msg-1",
		ReplyToContent: msg.Content, ReplyToMedia: "review.mp4=" + reviewSHA, ReplyToMediaPaths: []string{download},
		ReplyToMediaCount: 1, ReplyToMediaComplete: true, ReplyToAuthorID: "vf-bot", ChannelBotUserID: "vf-bot",
		ApprovalSenderAllowed: true, ApprovalPublishTarget: "reels",
	}
	if mutate != nil {
		mutate(rc)
	}
	res := f.tool.Execute(store.WithRunContext(ctx, rc), map[string]any{
		"action": "post", "channel": "fb-page", "target": "reels", "message": approvedReplyPayloadToken,
		"forward": true, "forward_reason": "duyệt",
	})
	return res, dispatched
}

func TestReelsMasterDraft_SendsGatewayCutAndSignedRecord(t *testing.T) {
	f := newMasterFixture(t)
	res := f.send(f.sendCtx(), reelsReviewText, f.master)
	if res.IsError {
		t.Fatalf("send failed: %s", res.ForLLM)
	}
	msg := f.sent(t)
	if len(msg.Media) != 1 || filepath.Base(msg.Media[0].URL) != "review.mp4" || msg.Media[0].ContentType != "video/mp4" {
		t.Fatalf("attachment = %+v, want the gateway's review.mp4", msg.Media)
	}
	if msg.Metadata[MetaContentFactoryReviewDraft] != "true" {
		t.Fatal("a master-mode draft must carry the review-draft marker so reactions can approve it")
	}
	lines := reelsMasterSHALines(msg.Content)
	masterSHA, _ := hashFeedPostFile(f.master)
	if len(lines) != 1 || lines[0] != masterSHA {
		t.Fatalf("master lines = %v, want [%s]", lines, masterSHA)
	}
	if caption, err := reelsCaption(msg.Content); err != nil || caption != reelsCaptionTxt {
		t.Fatalf("caption = %q, %v: the gateway line must stay outside the caption block", caption, err)
	}
	dirs := f.draftDirs(t)
	if len(dirs) != 1 {
		t.Fatalf("draft dirs = %v", dirs)
	}
	body, _ := os.ReadFile(filepath.Join(f.draftsRoot(), dirs[0], "record.json"))
	var rec reelsDraftRecord
	if err := json.Unmarshal(body, &rec); err != nil || rec.HMAC != rec.mac() || rec.MasterSHA256 != masterSHA {
		t.Fatalf("record = %+v (%v)", rec, err)
	}
	// The same master sent again gets its own draft and a distinct review cut.
	if res := f.send(f.sendCtx(), reelsReviewText, f.master); res.IsError {
		t.Fatalf("second send failed: %s", res.ForLLM)
	}
	second := f.sent(t)
	a, _ := hashFeedPostFile(msg.Media[0].URL)
	b, _ := hashFeedPostFile(second.Media[0].URL)
	if a == b {
		t.Fatal("two drafts of one master must have distinct review digests")
	}
}

func TestReelsMasterDraft_SendRefusals(t *testing.T) {
	cases := map[string]struct {
		setup   func(t *testing.T, f *masterFixture) (message, path string)
		wantMsg string
	}{
		"agent MEDIA line": {func(t *testing.T, f *masterFixture) (string, string) {
			return reelsReviewText + "\nMEDIA:" + f.master, f.master
		}, "replaces MEDIA"},
		"no caption block": {func(t *testing.T, f *masterFixture) (string, string) {
			return "just text", f.master
		}, "caption"},
		"outside the workspace": {func(t *testing.T, f *masterFixture) (string, string) {
			outside := filepath.Join(t.TempDir(), "master.mp4")
			_ = os.WriteFile(outside, []byte("\x00\x00\x00\x20ftypisom"), 0o644)
			return reelsReviewText, outside
		}, "inside your workspace"},
		"leaf symlink": {func(t *testing.T, f *masterFixture) (string, string) {
			link := filepath.Join(f.workspace, "link.mp4")
			_ = os.Symlink(f.master, link)
			return reelsReviewText, link
		}, "not a regular file"},
		"ancestor symlink out of the workspace": {func(t *testing.T, f *masterFixture) (string, string) {
			outside := t.TempDir()
			_ = os.WriteFile(filepath.Join(outside, "master.mp4"), []byte("\x00\x00\x00\x20ftypisom"), 0o644)
			_ = os.Symlink(outside, filepath.Join(f.workspace, "escape"))
			return reelsReviewText, filepath.Join(f.workspace, "escape", "master.mp4")
		}, "rejected"},
		"hard link": {func(t *testing.T, f *masterFixture) (string, string) {
			_ = os.Link(f.master, filepath.Join(f.workspace, "twin.mp4"))
			return reelsReviewText, f.master
		}, "hard link"},
		"not an mp4 box": {func(t *testing.T, f *masterFixture) (string, string) {
			_ = os.WriteFile(f.master, []byte("#EXTM3U\n#EXT-X-VERSION:3\n"), 0o644)
			return reelsReviewText, f.master
		}, "not an MP4"},
		"two video streams": {func(t *testing.T, f *masterFixture) (string, string) {
			fakeMediaTools(t, `{"streams":[{"codec_type":"video","codec_name":"h264","width":1080,"height":1920,"avg_frame_rate":"30/1"},`+
				`{"codec_type":"video","codec_name":"h264","width":1080,"height":1920,"avg_frame_rate":"30/1"}],"format":{"duration":"40"}}`, "")
			return reelsReviewText, f.master
		}, "one H.264 video"},
		"data stream": {func(t *testing.T, f *masterFixture) (string, string) {
			fakeMediaTools(t, `{"streams":[{"codec_type":"video","codec_name":"h264","width":1080,"height":1920,"avg_frame_rate":"30/1"},`+
				`{"codec_type":"data","codec_name":"bin_data"}],"format":{"duration":"40"}}`, "")
			return reelsReviewText, f.master
		}, "unsupported data stream"},
		"4K geometry": {func(t *testing.T, f *masterFixture) (string, string) {
			fakeMediaTools(t, strings.ReplaceAll(strings.ReplaceAll(fakeProbeOK, "1080", "2160"), "1920", "3840"), "")
			return reelsReviewText, f.master
		}, "not a Video Factory format"},
		"too long": {func(t *testing.T, f *masterFixture) (string, string) {
			fakeMediaTools(t, strings.Replace(fakeProbeOK, "45.7", "120", 1), "")
			return reelsReviewText, f.master
		}, "Reels need"},
		"untrusted tools": {func(t *testing.T, f *masterFixture) (string, string) {
			old := mediaTools
			mediaTools = trustedMediaTools
			mediaToolDirOld := mediaToolDir
			mediaToolDir = t.TempDir() // owned by the test user, readable binaries
			t.Cleanup(func() { mediaTools, mediaToolDir = old, mediaToolDirOld })
			return reelsReviewText, f.master
		}, "ffmpeg is not available"},
		"ffmpeg timeout": {func(t *testing.T, f *masterFixture) (string, string) {
			fakeMediaTools(t, fakeProbeOK, "while :; do :; done")
			old := reviewCutTimeout
			reviewCutTimeout = 300 * time.Millisecond
			t.Cleanup(func() { reviewCutTimeout = old })
			return reelsReviewText, f.master
		}, "timed out"},
		"slot busy": {func(t *testing.T, f *masterFixture) (string, string) {
			slot := reelsDeriveSlot(f.tenant.String())
			slot <- struct{}{}
			t.Cleanup(func() { <-slot })
			return reelsReviewText, f.master
		}, "being prepared"},
		"gateway dumpable": {func(t *testing.T, f *masterFixture) (string, string) {
			gatewayNonDumpable = func() bool { return false }
			return reelsReviewText, f.master
		}, "not hardened"},
		"not a Reels review chat": {func(t *testing.T, f *masterFixture) (string, string) {
			f.tool.SetReelsReviewChatChecker(func(string, string) bool { return false })
			return reelsReviewText, f.master
		}, "Reels review channel"},
		"no review chat checker": {func(t *testing.T, f *masterFixture) (string, string) {
			f.tool.SetReelsReviewChatChecker(nil)
			return reelsReviewText, f.master
		}, "Reels review channel"},
		"store full": {func(t *testing.T, f *masterFixture) (string, string) {
			old := reelsDraftsMaxBytes
			reelsDraftsMaxBytes = 1 << 10
			t.Cleanup(func() { reelsDraftsMaxBytes = old })
			return reelsReviewText, f.master
		}, "store is full"},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			f := newMasterFixture(t)
			message, path := tc.setup(t, f)
			res := f.send(f.sendCtx(), message, path)
			if !res.IsError || !strings.Contains(res.ForLLM, tc.wantMsg) {
				t.Fatalf("result = %+v, want an error containing %q", res, tc.wantMsg)
			}
			if dirs := f.draftDirs(t); len(dirs) != 0 {
				t.Fatalf("a refused send left draft dirs %v", dirs)
			}
			ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
			defer cancel()
			if _, ok := f.bus.SubscribeOutbound(ctx); ok {
				t.Fatal("a refused send published a message")
			}
		})
	}
}

func TestReelsMasterDraft_AgentWrittenMasterLineRefused(t *testing.T) {
	f := newMasterFixture(t)
	res := f.tool.Execute(f.sendCtx(), map[string]any{
		"action": "send", "channel": "vf-discord", "target": masterReviewChat,
		"message": reelsReviewText + "\nmaster_sha256: " + strings.Repeat("a", 64) + "\nMEDIA:" + f.master,
	})
	if !res.IsError || !strings.Contains(res.ForLLM, "master_sha256") {
		t.Fatalf("result = %+v, want the agent-written line refused", res)
	}
}

func TestReelsMasterApproval_PublishesTheSignedMaster(t *testing.T) {
	f := newMasterFixture(t)
	if res := f.send(f.sendCtx(), reelsReviewText, f.master); res.IsError {
		t.Fatal(res.ForLLM)
	}
	msg := f.sent(t)
	res, dispatched := f.approve(t, msg, nil)
	if res.IsError || len(dispatched) != 1 {
		t.Fatalf("approval = %+v, dispatched %d", res, len(dispatched))
	}
	out := dispatched[0]
	masterSHA, _ := hashFeedPostFile(f.master)
	if out.Metadata["reels_source"] != "master" || out.Metadata["approved_media_sha256"] != masterSHA {
		t.Fatalf("metadata = %+v, want the master published", out.Metadata)
	}
	if out.Content != reelsCaptionTxt {
		t.Fatalf("caption = %q", out.Content)
	}
	if dirs := f.draftDirs(t); len(dirs) != 0 {
		t.Fatalf("the posted draft was not removed: %v", dirs)
	}
}

func TestReelsMasterApproval_SecondApprovalSaysPublished(t *testing.T) {
	f := newMasterFixture(t)
	if res := f.send(f.sendCtx(), reelsReviewText, f.master); res.IsError {
		t.Fatal(res.ForLLM)
	}
	msg := f.sent(t)
	if res, dispatched := f.approve(t, msg, nil); res.IsError || len(dispatched) != 1 {
		t.Fatalf("first approval = %+v, dispatched %d", res, len(dispatched))
	}
	res, dispatched := f.approve(t, msg, nil)
	if !res.IsError || len(dispatched) != 0 || !strings.Contains(res.ForLLM, "already published") {
		t.Fatalf("second approval = %+v, dispatched %d; want \"already published\", never \"re-package\"", res, len(dispatched))
	}
}

func TestReelsMasterApproval_Refusals(t *testing.T) {
	type tamper func(t *testing.T, f *masterFixture, dir string, msg *bus.OutboundMessage) func(*store.RunContext)
	cases := map[string]tamper{
		"record swept": func(t *testing.T, f *masterFixture, dir string, _ *bus.OutboundMessage) func(*store.RunContext) {
			_ = os.RemoveAll(dir)
			return nil
		},
		"review swapped before upload": func(t *testing.T, f *masterFixture, _ string, msg *bus.OutboundMessage) func(*store.RunContext) {
			_ = os.WriteFile(msg.Media[0].URL, []byte("benign other video"), 0o600)
			return nil
		},
		"record rewritten": func(t *testing.T, f *masterFixture, dir string, _ *bus.OutboundMessage) func(*store.RunContext) {
			path := filepath.Join(dir, "record.json")
			body, _ := os.ReadFile(path)
			var rec reelsDraftRecord
			_ = json.Unmarshal(body, &rec)
			rec.MasterSHA256 = strings.Repeat("b", 64) // HMAC left as it was: the key is not the agent's
			body, _ = json.Marshal(rec)
			_ = os.WriteFile(path, body, 0o600)
			return nil
		},
		"master mutated after send": func(t *testing.T, f *masterFixture, dir string, _ *bus.OutboundMessage) func(*store.RunContext) {
			_ = os.WriteFile(filepath.Join(dir, "master.mp4"), []byte("\x00\x00\x00\x20ftypisom-other-video"), 0o600)
			return nil
		},
		"caption changed": func(t *testing.T, f *masterFixture, _ string, msg *bus.OutboundMessage) func(*store.RunContext) {
			msg.Content = strings.Replace(msg.Content, "cho chuyến đi tới", "cho chuyến đi khác", 1)
			return nil
		},
		"other tenant": func(t *testing.T, f *masterFixture, _ string, _ *bus.OutboundMessage) func(*store.RunContext) {
			return func(rc *store.RunContext) { rc.TenantID = uuid.MustParse("0193a5b0-7000-7000-8000-000000000002") }
		},
		"record expired (re-signed, so only the age fails)": func(t *testing.T, f *masterFixture, dir string, _ *bus.OutboundMessage) func(*store.RunContext) {
			path := filepath.Join(dir, "record.json")
			body, _ := os.ReadFile(path)
			var rec reelsDraftRecord
			_ = json.Unmarshal(body, &rec)
			rec.CreatedAt = time.Now().Add(-reelsDraftMaxAge - time.Hour)
			rec.HMAC = rec.mac()
			body, _ = json.Marshal(rec)
			_ = os.WriteFile(path, body, 0o600)
			return nil
		},
		"gateway restarted (new signing key)": func(t *testing.T, f *masterFixture, _ string, _ *bus.OutboundMessage) func(*store.RunContext) {
			old := reelsDraftKey
			reelsDraftKey = []byte("a different process key, 32 bytes")
			t.Cleanup(func() { reelsDraftKey = old })
			return nil
		},
		"two master lines": func(t *testing.T, f *masterFixture, _ string, msg *bus.OutboundMessage) func(*store.RunContext) {
			msg.Content += "\nmaster_sha256: " + strings.Repeat("c", 64)
			return nil
		},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			f := newMasterFixture(t)
			if res := f.send(f.sendCtx(), reelsReviewText, f.master); res.IsError {
				t.Fatal(res.ForLLM)
			}
			msg := f.sent(t)
			dir := filepath.Join(f.draftsRoot(), f.draftDirs(t)[0])
			mutate := tc(t, f, dir, &msg)
			res, dispatched := f.approve(t, msg, mutate)
			if !res.IsError || len(dispatched) != 0 {
				t.Fatalf("approval = %+v, dispatched %d; want a refusal and nothing published", res, len(dispatched))
			}
		})
	}
}
