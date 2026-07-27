package tools

import (
	"context"
	"encoding/json"
	"fmt"
	"maps"
	"math"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/nextlevelbuilder/goclaw/internal/bus"
	"github.com/nextlevelbuilder/goclaw/internal/store"
)

// outsidePath returns an absolute path that is guaranteed to be outside the
// given workspace and temp directories on any OS.  On Windows bare "/etc/..."
// is relative (no drive letter), so we prepend the volume name of the workspace
// to ensure filepath.IsAbs returns true.
func outsidePath(workspace, segments string) string {
	vol := filepath.VolumeName(workspace)
	return filepath.Join(vol+string(filepath.Separator), segments)
}

func TestResolveMediaPath(t *testing.T) {
	tmpDir := os.TempDir()

	// Create a temp workspace with a test file for workspace-relative tests.
	workspace := t.TempDir()
	docsDir := filepath.Join(workspace, "docs")
	if err := os.MkdirAll(docsDir, 0o755); err != nil {
		t.Fatal(err)
	}
	testFile := filepath.Join(docsDir, "report.pdf")
	if err := os.WriteFile(testFile, []byte("test"), 0o644); err != nil {
		t.Fatal(err)
	}

	// Normalize paths to canonical form (resolves macOS /var/folders → /private/var/folders symlink).
	// The resolvePath function uses filepath.EvalSymlinks, so test expectations must too.
	testFileCanonical, _ := filepath.EvalSymlinks(testFile)
	workspaceCanonical, _ := filepath.EvalSymlinks(workspace)

	t.Run("restricted", func(t *testing.T) {
		tool := NewMessageTool(workspaceCanonical, true)
		ctx := context.Background()

		tests := []struct {
			name   string
			input  string
			want   string
			wantOK bool
		}{
			// /tmp/ always allowed
			{"valid temp file", "MEDIA:" + filepath.Join(tmpDir, "test.png"), filepath.Join(tmpDir, "test.png"), true},
			{"valid nested temp", "MEDIA:" + filepath.Join(tmpDir, "sub", "file.txt"), filepath.Join(tmpDir, "sub", "file.txt"), true},

			// Workspace files allowed
			{"workspace absolute", "MEDIA:" + testFileCanonical, testFileCanonical, true},
			{"workspace relative", "MEDIA:docs/report.pdf", testFileCanonical, true},

			// Not a MEDIA: message
			{"no prefix", filepath.Join(tmpDir, "test.png"), "", false},
			{"empty after prefix", "MEDIA:", "", false},
			{"dot path", "MEDIA:.", "", false},
			{"empty string", "", "", false},
			{"just MEDIA", "MEDIA", "", false},

			// Outside workspace + outside /tmp/ → blocked
			{"outside workspace", "MEDIA:" + outsidePath(workspaceCanonical, "etc/passwd"), "", false},
			{"traversal attack", "MEDIA:" + filepath.Join(workspaceCanonical, "..", "etc", "passwd"), "", false},
		}

		for _, tt := range tests {
			t.Run(tt.name, func(t *testing.T) {
				got, ok := tool.resolveMediaPath(ctx, tt.input)
				if ok != tt.wantOK {
					t.Errorf("resolveMediaPath(%q) ok = %v, want %v", tt.input, ok, tt.wantOK)
				}
				if ok && got != tt.want {
					t.Errorf("resolveMediaPath(%q) = %q, want %q", tt.input, got, tt.want)
				}
			})
		}
	})

	// effectiveRestrict() always returns true (multi-tenant security hardening),
	// so even tools created with restrict=false behave as restricted.
	t.Run("unrestricted_tool_still_restricted", func(t *testing.T) {
		tool := NewMessageTool(workspaceCanonical, false)
		ctx := context.Background()

		tests := []struct {
			name   string
			input  string
			wantOK bool
		}{
			// Outside workspace → blocked (effectiveRestrict overrides to true)
			{"absolute outside workspace", "MEDIA:" + outsidePath(workspaceCanonical, "etc/hostname"), false},
			// Workspace-relative → allowed
			{"workspace relative", "MEDIA:docs/report.pdf", true},
			// /tmp/ → allowed (temp dir exception in restricted mode)
			{"temp file", "MEDIA:" + filepath.Join(tmpDir, "test.png"), true},
		}

		for _, tt := range tests {
			t.Run(tt.name, func(t *testing.T) {
				_, ok := tool.resolveMediaPath(ctx, tt.input)
				if ok != tt.wantOK {
					t.Errorf("resolveMediaPath(%q) ok = %v, want %v", tt.input, ok, tt.wantOK)
				}
			})
		}
	})

	t.Run("context workspace override", func(t *testing.T) {
		// Tool has no workspace, but context provides one.
		tool := NewMessageTool("", true)
		ctx := WithToolWorkspace(context.Background(), workspaceCanonical)

		got, ok := tool.resolveMediaPath(ctx, "MEDIA:docs/report.pdf")
		if !ok {
			t.Fatal("expected ok=true for workspace-relative path with context workspace")
		}
		if got != testFileCanonical {
			t.Errorf("got %q, want %q", got, testFileCanonical)
		}
	})
}

func TestIsInTempDir(t *testing.T) {
	tmpDir := os.TempDir()
	tests := []struct {
		name string
		path string
		want bool
	}{
		{"in tmp", filepath.Join(tmpDir, "test.png"), true},
		{"nested in tmp", filepath.Join(tmpDir, "sub", "file.txt"), true},
		{"tmp itself", tmpDir, false}, // only files inside, not the dir itself
		{"outside tmp", outsidePath(tmpDir, "etc/passwd"), false},
		{"relative path", "relative/path.txt", false},
		{"traversal", filepath.Join(tmpDir, "..", "etc", "passwd"), false},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := isInTempDir(tt.path); got != tt.want {
				t.Errorf("isInTempDir(%q) = %v, want %v", tt.path, got, tt.want)
			}
		})
	}
}

func TestExtractEmbeddedMedia(t *testing.T) {
	tmpDir := os.TempDir()

	workspace := t.TempDir()
	workspaceCanonical, _ := filepath.EvalSymlinks(workspace)

	// Create test files in workspace.
	docsDir := filepath.Join(workspaceCanonical, "docs")
	os.MkdirAll(docsDir, 0o755)
	reportFile := filepath.Join(docsDir, "report.docx")
	os.WriteFile(reportFile, []byte("test"), 0o644)
	reportCanonical, _ := filepath.EvalSymlinks(reportFile)

	tool := NewMessageTool(workspaceCanonical, true)
	ctx := context.Background()

	t.Run("no MEDIA: in message", func(t *testing.T) {
		msg := "Hello, here is your report!"
		cleaned, media := tool.extractEmbeddedMedia(ctx, msg)
		if cleaned != msg {
			t.Errorf("expected unchanged message, got %q", cleaned)
		}
		if len(media) != 0 {
			t.Errorf("expected no media, got %d", len(media))
		}
	})

	t.Run("embedded MEDIA: in multi-line message", func(t *testing.T) {
		msg := "Here is the file:\nMEDIA:" + reportCanonical + "\nPlease download!"
		cleaned, media := tool.extractEmbeddedMedia(ctx, msg)

		if cleaned != "Here is the file:\nPlease download!" {
			t.Errorf("unexpected cleaned text: %q", cleaned)
		}
		if len(media) != 1 {
			t.Fatalf("expected 1 media, got %d", len(media))
		}
		if media[0].URL != reportCanonical {
			t.Errorf("media URL = %q, want %q", media[0].URL, reportCanonical)
		}
		wantMime := "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
		if media[0].ContentType != wantMime {
			t.Errorf("content type = %q, want %q", media[0].ContentType, wantMime)
		}
	})

	t.Run("MEDIA: mid-sentence keeps surrounding text", func(t *testing.T) {
		msg := "Here is your report MEDIA:" + reportCanonical + " please review"
		cleaned, media := tool.extractEmbeddedMedia(ctx, msg)

		if cleaned != "Here is your report  please review" {
			t.Errorf("surrounding text lost: %q", cleaned)
		}
		if len(media) != 1 {
			t.Fatalf("expected 1 media, got %d", len(media))
		}
	})

	t.Run("multiple MEDIA: on same line", func(t *testing.T) {
		img := filepath.Join(tmpDir, "photo.png")
		msg := "MEDIA:" + reportCanonical + " MEDIA:" + img
		cleaned, media := tool.extractEmbeddedMedia(ctx, msg)

		if cleaned != "" {
			t.Errorf("expected empty cleaned text, got %q", cleaned)
		}
		if len(media) != 2 {
			t.Fatalf("expected 2 media from same line, got %d", len(media))
		}
	})

	t.Run("MEDIA: path outside workspace is stripped but no attachment", func(t *testing.T) {
		msg := "File:\nMEDIA:" + outsidePath(workspaceCanonical, "etc/passwd") + "\nDone"
		cleaned, media := tool.extractEmbeddedMedia(ctx, msg)

		if cleaned != "File:\nDone" {
			t.Errorf("MEDIA: line not stripped: %q", cleaned)
		}
		if len(media) != 0 {
			t.Errorf("expected no media for outside-workspace path, got %d", len(media))
		}
	})

	t.Run("message with only MEDIA: lines", func(t *testing.T) {
		msg := "MEDIA:" + reportCanonical
		cleaned, media := tool.extractEmbeddedMedia(ctx, msg)

		if cleaned != "" {
			t.Errorf("expected empty cleaned text, got %q", cleaned)
		}
		if len(media) != 1 {
			t.Fatalf("expected 1 media, got %d", len(media))
		}
	})

	t.Run("audio_as_voice tag stripped", func(t *testing.T) {
		msg := "[[audio_as_voice]]\nMEDIA:" + filepath.Join(tmpDir, "voice.ogg") + "\nExtra text"
		cleaned, media := tool.extractEmbeddedMedia(ctx, msg)

		if cleaned != "Extra text" {
			t.Errorf("unexpected cleaned text: %q", cleaned)
		}
		if len(media) != 1 {
			t.Fatalf("expected 1 media, got %d", len(media))
		}
	})

	t.Run("multiple MEDIA: paths", func(t *testing.T) {
		img := filepath.Join(tmpDir, "photo.png")
		msg := "Files:\nMEDIA:" + reportCanonical + "\nMEDIA:" + img + "\nEnjoy!"
		cleaned, media := tool.extractEmbeddedMedia(ctx, msg)

		if cleaned != "Files:\nEnjoy!" {
			t.Errorf("unexpected cleaned text: %q", cleaned)
		}
		if len(media) != 2 {
			t.Fatalf("expected 2 media, got %d", len(media))
		}
	})
}

func TestMimeFromPath(t *testing.T) {
	tests := []struct {
		path string
		want string
	}{
		{"/tmp/file.png", "image/png"},
		{"/tmp/file.jpg", "image/jpeg"},
		{"/tmp/file.mp4", "video/mp4"},
		{"/tmp/file.ogg", "audio/ogg"},
		{"/tmp/file.pdf", "application/pdf"},
		{"/tmp/file.doc", "application/msword"},
		{"/tmp/file.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
		{"/tmp/file.xls", "application/vnd.ms-excel"},
		{"/tmp/file.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
		{"/tmp/file.unknown", "application/octet-stream"},
	}
	for _, tt := range tests {
		t.Run(filepath.Base(tt.path), func(t *testing.T) {
			if got := mimeFromPath(tt.path); got != tt.want {
				t.Errorf("mimeFromPath(%q) = %q, want %q", tt.path, got, tt.want)
			}
		})
	}
}

func TestValidateChannelTenant(t *testing.T) {
	tenantA := uuid.MustParse("0193a5b0-7000-7000-8000-000000000001")
	tenantB := uuid.MustParse("019d1135-7087-7aa9-a2c7-cdaf7af851b1")

	tool := NewMessageTool("", true)

	t.Run("no checker configured allows all", func(t *testing.T) {
		ctx := store.WithTenantID(context.Background(), tenantA)
		if err := tool.validateChannelTenant(ctx, "telegram", "123"); err != nil {
			t.Errorf("expected nil, got error: %s", err.ForLLM)
		}
	})

	// Wire a mock checker.
	channels := map[string]uuid.UUID{
		"telegram":    tenantA,
		"tenant-b-tg": tenantB,
	}
	tool.SetChannelTenantChecker(func(name string) (uuid.UUID, bool) {
		tid, ok := channels[name]
		return tid, ok
	})

	t.Run("same tenant allows", func(t *testing.T) {
		ctx := store.WithTenantID(context.Background(), tenantA)
		if err := tool.validateChannelTenant(ctx, "telegram", "123"); err != nil {
			t.Errorf("expected nil for same tenant, got: %s", err.ForLLM)
		}
	})

	t.Run("cross tenant blocks", func(t *testing.T) {
		ctx := store.WithTenantID(context.Background(), tenantA)
		err := tool.validateChannelTenant(ctx, "tenant-b-tg", "456")
		if err == nil {
			t.Fatal("expected error for cross-tenant send, got nil")
		}
		if !err.IsError {
			t.Error("expected IsError=true")
		}
	})

	t.Run("channel not found blocks", func(t *testing.T) {
		ctx := store.WithTenantID(context.Background(), tenantA)
		err := tool.validateChannelTenant(ctx, "nonexistent", "789")
		if err == nil {
			t.Fatal("expected error for missing channel, got nil")
		}
	})

	t.Run("nil channel tenant allows (legacy)", func(t *testing.T) {
		channels["legacy-ch"] = uuid.Nil
		ctx := store.WithTenantID(context.Background(), tenantA)
		if err := tool.validateChannelTenant(ctx, "legacy-ch", "123"); err != nil {
			t.Errorf("expected nil for legacy channel, got: %s", err.ForLLM)
		}
	})

	t.Run("nil context tenant allows (master/system)", func(t *testing.T) {
		ctx := context.Background() // no tenant in context
		if err := tool.validateChannelTenant(ctx, "tenant-b-tg", "456"); err != nil {
			t.Errorf("expected nil for master context, got: %s", err.ForLLM)
		}
	})
}

func TestSelfSendGuard(t *testing.T) {
	workspace := t.TempDir()
	workspaceCanonical, _ := filepath.EvalSymlinks(workspace)

	// Create a test file for MEDIA: resolution.
	testFile := filepath.Join(workspaceCanonical, "report.csv")
	os.WriteFile(testFile, []byte("data"), 0o644)

	tool := NewMessageTool(workspaceCanonical, true)
	// Wire message bus so MEDIA sends can proceed past self-send guard.
	tool.SetMessageBus(bus.New())

	// Build context with self-send channel/chatID.
	mkCtx := func() context.Context {
		ctx := context.Background()
		ctx = WithToolChannel(ctx, "telegram")
		ctx = WithToolChatID(ctx, "chat-42")
		return ctx
	}

	t.Run("text self-send blocked", func(t *testing.T) {
		result := tool.Execute(mkCtx(), map[string]any{
			"action":  "send",
			"channel": "telegram",
			"target":  "chat-42",
			"message": "Hello, this is a text message",
		})
		if !result.IsError {
			t.Fatal("expected text self-send to be blocked")
		}
	})

	t.Run("text to different chat allowed", func(t *testing.T) {
		// Cross-target guard also kicks in on unbound sessions — supply
		// forward=true so this test isolates the self-send guard.
		result := tool.Execute(mkCtx(), map[string]any{
			"action":         "send",
			"channel":        "telegram",
			"target":         "chat-99",
			"message":        "Hello, other chat",
			"forward":        true,
			"forward_reason": "test cross-chat",
		})
		if result.IsError {
			t.Fatalf("expected cross-chat send to succeed, got: %s", result.ForLLM)
		}
	})

	t.Run("MEDIA self-send allowed when not delivered", func(t *testing.T) {
		// No delivered media tracker — MEDIA self-send should be allowed.
		result := tool.Execute(mkCtx(), map[string]any{
			"action":  "send",
			"channel": "telegram",
			"target":  "chat-42",
			"message": "MEDIA:" + testFile,
		})
		if result.IsError {
			t.Fatalf("expected MEDIA self-send to be allowed, got: %s", result.ForLLM)
		}
	})

	t.Run("MEDIA self-send blocked when already delivered", func(t *testing.T) {
		ctx := mkCtx()
		dm := NewDeliveredMedia()
		dm.Mark(testFile)
		ctx = WithDeliveredMedia(ctx, dm)

		result := tool.Execute(ctx, map[string]any{
			"action":  "send",
			"channel": "telegram",
			"target":  "chat-42",
			"message": "MEDIA:" + testFile,
		})
		if !result.IsError {
			t.Fatal("expected MEDIA self-send to be blocked when file already delivered")
		}
	})

	t.Run("MEDIA self-send allowed for undelivered file with tracker", func(t *testing.T) {
		ctx := mkCtx()
		dm := NewDeliveredMedia()
		dm.Mark("/some/other/file.pdf") // different file marked
		ctx = WithDeliveredMedia(ctx, dm)

		result := tool.Execute(ctx, map[string]any{
			"action":  "send",
			"channel": "telegram",
			"target":  "chat-42",
			"message": "MEDIA:" + testFile,
		})
		if result.IsError {
			t.Fatalf("expected MEDIA self-send for undelivered file to be allowed, got: %s", result.ForLLM)
		}
	})

	t.Run("embedded MEDIA in text self-send blocked", func(t *testing.T) {
		ctx := mkCtx()
		dm := NewDeliveredMedia()
		dm.Mark(testFile)
		ctx = WithDeliveredMedia(ctx, dm)

		result := tool.Execute(ctx, map[string]any{
			"action":  "send",
			"channel": "telegram",
			"target":  "chat-42",
			"message": "Here is the file\nMEDIA:" + testFile,
		})
		// Contains MEDIA: pattern → passes text guard → but file is delivered → blocked
		if !result.IsError {
			t.Fatal("expected embedded MEDIA self-send to be blocked when file already delivered")
		}
	})
}

func TestMessageToolNumericTargetUsesSendPath(t *testing.T) {
	// JSON tool args use float64 for integers; target must not be ignored (was only .(string)).
	var gotChat string
	tool := NewMessageTool("", true)
	tool.SetChannelSender(func(_ context.Context, ch, chatID, content string) error {
		if ch != "telegram" {
			t.Errorf("channel = %q", ch)
		}
		gotChat = chatID
		return nil
	})
	ctx := context.Background()
	r := tool.Execute(ctx, map[string]any{
		"action":  "send",
		"channel": "telegram",
		"target":  float64(-1001847298537),
		"message": "hello",
	})
	if r.IsError {
		t.Fatalf("unexpected error: %s", r.ForLLM)
	}
	if gotChat != "-1001847298537" {
		t.Errorf("sender saw chatID %q, want -1001847298537", gotChat)
	}
}

func TestArgString(t *testing.T) {
	tests := []struct {
		name string
		args map[string]any
		key  string
		want string
	}{
		{"string value", map[string]any{"k": "hello"}, "k", "hello"},
		{"string with spaces", map[string]any{"k": "  hi  "}, "k", "hi"},
		{"empty string", map[string]any{"k": ""}, "k", ""},
		{"missing key", map[string]any{}, "k", ""},
		{"nil value", map[string]any{"k": nil}, "k", ""},
		{"float64 integer", map[string]any{"k": float64(-1001847298537)}, "k", "-1001847298537"},
		{"float64 positive", map[string]any{"k": float64(42)}, "k", "42"},
		{"float64 zero", map[string]any{"k": float64(0)}, "k", "0"},
		{"float64 NaN", map[string]any{"k": math.NaN()}, "k", ""},
		{"int", map[string]any{"k": 123}, "k", "123"},
		{"int64", map[string]any{"k": int64(-999)}, "k", "-999"},
		{"json.Number", map[string]any{"k": json.Number("7654321")}, "k", "7654321"},
		{"bool fallback", map[string]any{"k": true}, "k", "true"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := argString(tt.args, tt.key)
			if got != tt.want {
				t.Errorf("argString(%v, %q) = %q, want %q", tt.args, tt.key, got, tt.want)
			}
		})
	}
}

func TestDeliveredMedia(t *testing.T) {
	dm := NewDeliveredMedia()

	if dm.IsDelivered("/tmp/test.csv") {
		t.Fatal("expected empty tracker to report false")
	}

	dm.Mark("/tmp/test.csv")
	if !dm.IsDelivered("/tmp/test.csv") {
		t.Fatal("expected marked path to be delivered")
	}

	if dm.IsDelivered("/tmp/other.csv") {
		t.Fatal("expected unmarked path to report false")
	}
}

func TestMessageToolCrossTargetGuard(t *testing.T) {
	type scenario struct {
		name         string
		sessionKey   string
		ctxChannel   string
		ctxChatID    string
		peerKind     string
		args         map[string]any
		wantErr      bool
		wantOutbound int // number of outbound messages published
		wantTargets  []string
	}

	const (
		agentDM    = "agent:a:telegram:direct:U1"
		agentGroup = "agent:a:telegram:group:-100G"
		agentCron  = "agent:a:cron:job-1"
		agentHB    = "agent:a:heartbeat"
		agentSub   = "agent:a:subagent:child-1"
		agentTeam  = "agent:a:team:T1:U1"
	)

	baseArgs := func(extra map[string]any) map[string]any {
		m := map[string]any{"action": "send", "message": "hello"}
		maps.Copy(m, extra)
		return m
	}

	scenarios := []scenario{
		{
			name:         "1_dm_same_target_pass",
			sessionKey:   agentDM,
			ctxChannel:   "telegram",
			ctxChatID:    "U1",
			peerKind:     "direct",
			args:         baseArgs(map[string]any{"channel": "telegram", "target": "U1"}),
			wantOutbound: 1,
			wantTargets:  []string{"U1"},
		},
		{
			name:       "2_dm_cross_target_blocked",
			sessionKey: agentDM,
			ctxChannel: "telegram",
			ctxChatID:  "U1",
			peerKind:   "direct",
			args:       baseArgs(map[string]any{"channel": "telegram", "target": "-1003787954683"}),
			wantErr:    true,
		},
		{
			name:         "3_dm_cross_target_forward_pass",
			sessionKey:   agentDM,
			ctxChannel:   "telegram",
			ctxChatID:    "U1",
			peerKind:     "direct",
			args:         baseArgs(map[string]any{"channel": "telegram", "target": "-100G", "forward": true, "forward_reason": "user asked to forward"}),
			wantOutbound: 2,
			wantTargets:  []string{"-100G", "U1"},
		},
		{
			name:         "4_group_same_target_pass",
			sessionKey:   agentGroup,
			ctxChannel:   "telegram",
			ctxChatID:    "-100G",
			peerKind:     "group",
			args:         baseArgs(map[string]any{"channel": "telegram", "target": "-100G"}),
			wantOutbound: 1,
			wantTargets:  []string{"-100G"},
		},
		{
			name:       "5_group_cross_target_blocked",
			sessionKey: agentGroup,
			ctxChannel: "telegram",
			ctxChatID:  "-100G",
			peerKind:   "group",
			args:       baseArgs(map[string]any{"channel": "telegram", "target": "-100G2"}),
			wantErr:    true,
		},
		{
			name:         "6_cron_free",
			sessionKey:   agentCron,
			ctxChannel:   "telegram",
			ctxChatID:    "U1",
			peerKind:     "direct",
			args:         baseArgs(map[string]any{"channel": "telegram", "target": "-100X"}),
			wantOutbound: 1,
			wantTargets:  []string{"-100X"},
		},
		{
			name:         "7_heartbeat_free",
			sessionKey:   agentHB,
			ctxChannel:   "telegram",
			ctxChatID:    "U1",
			peerKind:     "direct",
			args:         baseArgs(map[string]any{"channel": "telegram", "target": "-100X"}),
			wantOutbound: 1,
			wantTargets:  []string{"-100X"},
		},
		{
			name:         "7b_subagent_free",
			sessionKey:   agentSub,
			ctxChannel:   "telegram",
			ctxChatID:    "U1",
			peerKind:     "direct",
			args:         baseArgs(map[string]any{"channel": "telegram", "target": "-100X"}),
			wantOutbound: 1,
			wantTargets:  []string{"-100X"},
		},
		{
			name:         "7c_team_free",
			sessionKey:   agentTeam,
			ctxChannel:   "telegram",
			ctxChatID:    "U1",
			peerKind:     "direct",
			args:         baseArgs(map[string]any{"channel": "telegram", "target": "-100X"}),
			wantOutbound: 1,
			wantTargets:  []string{"-100X"},
		},
	}

	// For same-target DM/group scenarios, text self-sends are blocked by the
	// pre-existing self-send guard (different from our new cross-target guard).
	// Use MEDIA: to bypass that — our guard doesn't care about message kind.
	sharedTmp := t.TempDir()
	mediaPath := filepath.Join(sharedTmp, "note.png")
	if err := os.WriteFile(mediaPath, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	mediaMsg := "MEDIA:" + mediaPath
	// Apply MEDIA bypass to same-target scenarios.
	for i := range scenarios {
		if scenarios[i].name == "1_dm_same_target_pass" || scenarios[i].name == "4_group_same_target_pass" {
			scenarios[i].args["message"] = mediaMsg
		}
	}

	for _, sc := range scenarios {
		t.Run(sc.name, func(t *testing.T) {
			tool := NewMessageTool(sharedTmp, false)
			mb := bus.New()
			tool.SetMessageBus(mb)

			ctx := context.Background()
			ctx = WithToolSessionKey(ctx, sc.sessionKey)
			ctx = WithToolChannel(ctx, sc.ctxChannel)
			ctx = WithToolChatID(ctx, sc.ctxChatID)
			ctx = WithToolPeerKind(ctx, sc.peerKind)

			res := tool.Execute(ctx, sc.args)
			if sc.wantErr {
				if res == nil || !res.IsError {
					t.Fatalf("expected error result, got: %+v", res)
				}
				// Guard must not publish when blocked.
				got := drainBusNow(mb)
				if len(got) != 0 {
					t.Fatalf("expected 0 outbound on block, got %d: %+v", len(got), got)
				}
				return
			}
			if res != nil && res.IsError {
				t.Fatalf("unexpected error: %s", res.ForLLM)
			}
			got := drainBusNow(mb)
			if len(got) != sc.wantOutbound {
				t.Fatalf("outbound count: got %d want %d (%+v)", len(got), sc.wantOutbound, got)
			}
			for i, want := range sc.wantTargets {
				if got[i].ChatID != want {
					t.Errorf("outbound[%d].ChatID = %q, want %q", i, got[i].ChatID, want)
				}
			}
		})
	}
}

// drainBusNow reads all buffered outbound messages using a short timeout per
// read. Pre-cancelled ctx can't be used: select{msg,ctx.Done} picks randomly
// when both are ready, so buffered messages would be lost ~50% of the time.
func drainBusNow(mb *bus.MessageBus) []bus.OutboundMessage {
	var out []bus.OutboundMessage
	for {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Millisecond)
		msg, ok := mb.SubscribeOutbound(ctx)
		cancel()
		if !ok {
			return out
		}
		out = append(out, msg)
	}
}

// Test 10 — replay of production trace 019d9fcf-b433-7550-a9a6-62efa140128d:
// DM session, agent tried to send to a group topic ID. Guard must block.
func TestMessageToolCrossTargetGuard_TraceReplay(t *testing.T) {
	tool := NewMessageTool(t.TempDir(), false)
	mb := bus.New()
	tool.SetMessageBus(mb)

	ctx := context.Background()
	ctx = WithToolSessionKey(ctx, "agent:a:telegram:direct:U1")
	ctx = WithToolChannel(ctx, "telegram")
	ctx = WithToolChatID(ctx, "U1")
	ctx = WithToolPeerKind(ctx, "direct")

	res := tool.Execute(ctx, map[string]any{
		"action":  "send",
		"channel": "telegram",
		"target":  "-1003787954683:topic:1",
		"message": "here is the image",
	})
	if res == nil || !res.IsError {
		t.Fatalf("trace replay: expected ErrorResult, got: %+v", res)
	}
	if got := drainBusNow(mb); len(got) != 0 {
		t.Fatalf("trace replay: expected 0 outbound, got %d", len(got))
	}
}

// Sender-only deployment (no msgBus): notice falls back through t.sender so
// the origin chat still gets the audit breadcrumb. Verifies P1 fix.
func TestMessageToolCrossTargetGuard_NoticeFallbackSender(t *testing.T) {
	tool := NewMessageTool(t.TempDir(), false)
	type sent struct{ channel, target, message string }
	var calls []sent
	tool.SetChannelSender(func(_ context.Context, ch, tgt, msg string) error {
		calls = append(calls, sent{ch, tgt, msg})
		return nil
	})

	ctx := context.Background()
	ctx = WithToolSessionKey(ctx, "agent:a:telegram:direct:U1")
	ctx = WithToolChannel(ctx, "telegram")
	ctx = WithToolChatID(ctx, "U1")
	ctx = WithToolPeerKind(ctx, "direct")

	res := tool.Execute(ctx, map[string]any{
		"action": "send", "channel": "telegram", "target": "-100G",
		"forward": true, "forward_reason": "user asked forward",
		"message": "hello group",
	})
	if res == nil || res.IsError {
		t.Fatalf("expected success, got: %+v", res)
	}
	if len(calls) != 2 {
		t.Fatalf("expected 2 sender calls (forward + notice), got %d: %+v", len(calls), calls)
	}
	if calls[0].target != "-100G" {
		t.Errorf("forward target: got %q want -100G", calls[0].target)
	}
	if calls[1].target != "U1" {
		t.Errorf("notice target: got %q want U1 (origin)", calls[1].target)
	}
	if !strings.Contains(calls[1].message, "user asked forward") {
		t.Errorf("notice missing reason: %q", calls[1].message)
	}
}

// Notice must NOT post when the forward itself fails. Verifies P2 fix.
func TestMessageToolCrossTargetGuard_NoNoticeOnSendFailure(t *testing.T) {
	tool := NewMessageTool(t.TempDir(), false)
	var calls int
	tool.SetChannelSender(func(_ context.Context, _, _, _ string) error {
		calls++
		return fmt.Errorf("boom")
	})

	ctx := context.Background()
	ctx = WithToolSessionKey(ctx, "agent:a:telegram:direct:U1")
	ctx = WithToolChannel(ctx, "telegram")
	ctx = WithToolChatID(ctx, "U1")
	ctx = WithToolPeerKind(ctx, "direct")

	res := tool.Execute(ctx, map[string]any{
		"action": "send", "channel": "telegram", "target": "-100G",
		"forward": true, "forward_reason": "r",
		"message": "hi",
	})
	if res == nil || !res.IsError {
		t.Fatalf("expected ErrorResult on send failure, got: %+v", res)
	}
	if calls != 1 {
		t.Errorf("expected only 1 sender call (forward, no notice), got %d", calls)
	}
}

func TestMessageTargetEnforced(t *testing.T) {
	cases := []struct {
		key  string
		want bool
	}{
		{"", true},
		{"agent:a:telegram:direct:U1", true},
		{"agent:a:telegram:group:-100G", true},
		{"agent:a:ws:direct:conv-1", true},
		{"agent:a:main", true},
		{"agent:a:cron:job-1", false},
		{"agent:a:heartbeat", false},
		{"agent:a:heartbeat:12345", false},
		{"agent:a:subagent:child", false},
		{"agent:a:team:T1:U1", false},
	}
	for _, tc := range cases {
		if got := MessageTargetEnforced(tc.key); got != tc.want {
			t.Errorf("MessageTargetEnforced(%q) = %v, want %v", tc.key, got, tc.want)
		}
	}
}

// --- action="post" tests ---

const approvedFeedPostContent = "Approved article content"

func approvedFeedPostCtx() context.Context {
	return approvedFeedPostCtxFor(approvedFeedPostContent, "review-msg-1", "")
}

func boolToInt(value bool) int {
	if value {
		return 1
	}
	return 0
}

func approvedFeedPostCtxFor(content, replyID, replyMedia string) context.Context {
	ctx := context.Background()
	ctx = WithToolSessionKey(ctx, "agent:a:discord:group:"+contentFactoryApprovalChannelID)
	ctx = WithToolChannel(ctx, "discord-bot")
	ctx = WithToolChatID(ctx, contentFactoryApprovalChannelID)
	ctx = WithToolPeerKind(ctx, "group")
	return store.WithRunContext(ctx, &store.RunContext{
		AgentID:               uuid.MustParse("019d1b58-ae00-7b64-8594-89b6158f327b"),
		AgentKey:              "zip-crazy",
		TenantID:              uuid.MustParse("0193a5b0-7000-7000-8000-000000000001"),
		SenderID:              "approver-1",
		ChannelType:           "discord",
		InboundMessage:        "[Replying to GoClaw]\n" + content + "\n[/Replying]\n\nduyệt",
		ReplyToContent:        content,
		ReplyToMedia:          replyMedia,
		ReplyToMediaCount:     boolToInt(replyMedia != ""),
		ReplyToMediaComplete:  true,
		ReplyToAuthorID:       "discord-bot-user",
		ChannelBotUserID:      "discord-bot-user",
		ApprovalSenderAllowed: true,
		CurrentMessage:        "duyệt",
		ReplyToMessageID:      replyID,
	})
}

func TestMessagePost_SyncDispatch(t *testing.T) {
	tool := NewMessageTool(t.TempDir(), false)
	var dispatched bus.OutboundMessage
	tool.SetOutboundDispatcher(func(_ context.Context, msg bus.OutboundMessage) error {
		dispatched = msg
		return nil
	})

	res := tool.Execute(approvedFeedPostCtx(), map[string]any{
		"action":         "post",
		"channel":        "fb-page",
		"target":         "feed",
		"forward":        true,
		"forward_reason": "User replied 'duyệt' to this ContentFactory article",
		"message":        approvedFeedPostContent,
	})
	if res == nil || res.IsError {
		t.Fatalf("expected success, got: %+v", res)
	}
	if !strings.Contains(res.ForLLM, `"status":"posted"`) {
		t.Errorf("result = %q, want posted status", res.ForLLM)
	}
	if dispatched.Channel != "fb-page" {
		t.Errorf("channel = %q, want fb-page", dispatched.Channel)
	}
	if dispatched.Content != approvedFeedPostContent {
		t.Errorf("content = %q, want %q", dispatched.Content, approvedFeedPostContent)
	}
	if dispatched.Metadata["publisher_agent_id"] != "zip-crazy" {
		t.Errorf("publisher_agent_id = %q, want zip-crazy", dispatched.Metadata["publisher_agent_id"])
	}
	if dispatched.Metadata["fb_mode"] != "feed_post" {
		t.Errorf("fb_mode = %q, want feed_post", dispatched.Metadata["fb_mode"])
	}
}

func TestMessagePost_SyncDispatch_Error(t *testing.T) {
	tool := NewMessageTool(t.TempDir(), false)
	var calls atomic.Int32
	tool.SetOutboundDispatcher(func(_ context.Context, msg bus.OutboundMessage) error {
		calls.Add(1)
		return fmt.Errorf("Graph API: token expired at /private/path")
	})
	args := map[string]any{
		"action":         "post",
		"channel":        "fb-page",
		"target":         "feed",
		"forward":        true,
		"forward_reason": "User replied 'duyệt' to this ContentFactory article",
		"message":        approvedFeedPostContent,
	}

	res := tool.Execute(approvedFeedPostCtx(), args)
	if res == nil || !res.IsError {
		t.Fatal("expected ErrorResult on dispatch failure")
	}
	if !strings.Contains(res.ForLLM, "status is unknown") {
		t.Errorf("error = %q, want unknown status", res.ForLLM)
	}
	if strings.Contains(res.ForLLM, "token expired") || strings.Contains(res.ForLLM, "/private/path") {
		t.Fatalf("error leaked internal dispatch details: %q", res.ForLLM)
	}

	retry := tool.Execute(approvedFeedPostCtx(), args)
	if retry == nil || !retry.IsError || !strings.Contains(retry.ForLLM, "already reserved") {
		t.Fatalf("expected retry to be blocked by reservation, got: %+v", retry)
	}
	if got := calls.Load(); got != 1 {
		t.Fatalf("dispatcher called %d times, want 1", got)
	}
}

func TestMessagePost_RequiresSyncDispatcher(t *testing.T) {
	tool := NewMessageTool(t.TempDir(), false)
	tool.SetMessageBus(bus.New())

	res := tool.Execute(approvedFeedPostCtx(), map[string]any{
		"action":         "post",
		"channel":        "fb-page",
		"target":         "feed",
		"forward":        true,
		"forward_reason": "User replied 'duyệt' to this ContentFactory article",
		"message":        "Async post",
	})
	if res == nil || !res.IsError {
		t.Fatalf("expected ErrorResult without synchronous dispatcher, got: %+v", res)
	}
	if !strings.Contains(res.ForLLM, "requires synchronous outbound dispatcher") {
		t.Errorf("error = %q, want sync dispatcher requirement", res.ForLLM)
	}
}

func TestMessagePost_NoDispatcherNoBus(t *testing.T) {
	tool := NewMessageTool(t.TempDir(), false)
	// Neither dispatcher nor bus set.

	res := tool.Execute(approvedFeedPostCtx(), map[string]any{
		"action":         "post",
		"channel":        "fb-page",
		"target":         "feed",
		"forward":        true,
		"forward_reason": "User replied 'duyệt' to this ContentFactory article",
		"message":        "No backend",
	})
	if res == nil || !res.IsError {
		t.Fatal("expected ErrorResult when no dispatcher or bus")
	}
	if !strings.Contains(res.ForLLM, "requires synchronous outbound dispatcher") {
		t.Errorf("error = %q, want sync dispatcher requirement", res.ForLLM)
	}
}

func TestMessagePost_CrossTargetRequiresApprovalEvidence(t *testing.T) {
	tool := NewMessageTool(t.TempDir(), false)
	var dispatched bool
	tool.SetOutboundDispatcher(func(_ context.Context, msg bus.OutboundMessage) error {
		dispatched = true
		return nil
	})

	ctx := context.Background()
	ctx = WithToolSessionKey(ctx, "agent:a:discord:direct:user123")
	ctx = WithToolChannel(ctx, "discord")
	ctx = WithToolChatID(ctx, "user123")
	ctx = WithToolPeerKind(ctx, "direct")

	res := tool.Execute(ctx, map[string]any{
		"action":  "post",
		"channel": "fb-page",
		"message": "Approved article content",
	})
	if res == nil || !res.IsError {
		t.Fatalf("expected cross-target approval error, got: %+v", res)
	}
	if dispatched {
		t.Fatal("dispatcher must not be called without explicit approval evidence")
	}
}

func TestMessagePost_RejectsNegatedCurrentApproval(t *testing.T) {
	tool := NewMessageTool(t.TempDir(), false)
	var dispatched bool
	tool.SetOutboundDispatcher(func(_ context.Context, msg bus.OutboundMessage) error {
		dispatched = true
		return nil
	})

	ctx := approvedFeedPostCtx()
	ctx = store.WithRunContext(ctx, &store.RunContext{
		SenderID:         "approver-1",
		ChannelType:      "discord",
		InboundMessage:   "[Replying to GoClaw]\nApproved article content\n[/Replying]\n\nkhông duyệt",
		ReplyToContent:   "Approved article content",
		CurrentMessage:   "không duyệt",
		ReplyToMessageID: "review-msg-1",
	})
	res := tool.Execute(ctx, map[string]any{
		"action":         "post",
		"channel":        "fb-page",
		"target":         "feed",
		"forward":        true,
		"forward_reason": "User replied 'duyệt' to this ContentFactory article",
		"message":        "Approved article content",
	})
	if res == nil || !res.IsError {
		t.Fatalf("expected negated approval rejection, got: %+v", res)
	}
	if dispatched {
		t.Fatal("dispatcher must not be called for negated approval")
	}
}

func TestMessagePost_RejectsApprovalOutsideContentFactoryChannel(t *testing.T) {
	tool := NewMessageTool(t.TempDir(), false)
	var dispatched bool
	tool.SetOutboundDispatcher(func(_ context.Context, msg bus.OutboundMessage) error {
		dispatched = true
		return nil
	})

	ctx := approvedFeedPostCtx()
	ctx = WithToolChatID(ctx, "wrong-channel")
	res := tool.Execute(ctx, map[string]any{
		"action":         "post",
		"channel":        "fb-page",
		"target":         "feed",
		"forward":        true,
		"forward_reason": "User replied 'duyệt' to this ContentFactory article",
		"message":        "Approved article content",
	})
	if res == nil || !res.IsError {
		t.Fatalf("expected origin channel rejection, got: %+v", res)
	}
	if dispatched {
		t.Fatal("dispatcher must not be called for approval from wrong channel")
	}
}

func TestMessagePost_RejectsHumanAuthoredReviewMessage(t *testing.T) {
	tool := NewMessageTool(t.TempDir(), false)
	var dispatched bool
	tool.SetOutboundDispatcher(func(_ context.Context, msg bus.OutboundMessage) error {
		dispatched = true
		return nil
	})
	ctx := approvedFeedPostCtx()
	store.RunContextFromCtx(ctx).ReplyToAuthorID = "human-user"

	res := tool.Execute(ctx, map[string]any{
		"action":         "post",
		"channel":        "fb-page",
		"target":         "feed",
		"forward":        true,
		"forward_reason": "User replied 'duyệt' to this ContentFactory article",
		"message":        approvedFeedPostContent,
	})
	if res == nil || !res.IsError {
		t.Fatalf("expected human-authored review rejection, got: %+v", res)
	}
	if dispatched {
		t.Fatal("dispatcher must not be called for human-authored review content")
	}
}

func TestMessagePost_RejectsNonAllowlistedApprover(t *testing.T) {
	tool := NewMessageTool(t.TempDir(), false)
	var dispatched bool
	tool.SetOutboundDispatcher(func(_ context.Context, msg bus.OutboundMessage) error {
		dispatched = true
		return nil
	})
	ctx := approvedFeedPostCtx()
	store.RunContextFromCtx(ctx).ApprovalSenderAllowed = false

	res := tool.Execute(ctx, map[string]any{
		"action":         "post",
		"channel":        "fb-page",
		"target":         "feed",
		"forward":        true,
		"forward_reason": "User replied 'duyệt' to this ContentFactory article",
		"message":        approvedFeedPostContent,
	})
	if res == nil || !res.IsError {
		t.Fatalf("expected approver allowlist rejection, got: %+v", res)
	}
	if dispatched {
		t.Fatal("dispatcher must not be called for a non-allowlisted approver")
	}
}

func TestMessagePost_RejectsIncompleteReplyMediaEvidence(t *testing.T) {
	tool := NewMessageTool(t.TempDir(), false)
	var dispatched bool
	tool.SetOutboundDispatcher(func(_ context.Context, msg bus.OutboundMessage) error {
		dispatched = true
		return nil
	})
	ctx := approvedFeedPostCtx()
	rc := store.RunContextFromCtx(ctx)
	rc.ReplyToMediaCount = 1
	rc.ReplyToMediaComplete = false

	res := tool.Execute(ctx, map[string]any{
		"action":         "post",
		"channel":        "fb-page",
		"target":         "feed",
		"forward":        true,
		"forward_reason": "User replied 'duyệt' to this ContentFactory article",
		"message":        approvedFeedPostContent,
	})
	if res == nil || !res.IsError {
		t.Fatalf("expected incomplete media evidence rejection, got: %+v", res)
	}
	if dispatched {
		t.Fatal("dispatcher must not be called with incomplete media evidence")
	}
}

func TestMessagePost_CrossTargetExplicitApprovalPasses(t *testing.T) {
	tool := NewMessageTool(t.TempDir(), false)
	var dispatched bus.OutboundMessage
	tool.SetOutboundDispatcher(func(_ context.Context, msg bus.OutboundMessage) error {
		dispatched = msg
		return nil
	})

	res := tool.Execute(approvedFeedPostCtx(), map[string]any{
		"action":         "post",
		"channel":        "fb-page",
		"target":         "feed",
		"forward":        true,
		"forward_reason": "User replied 'duyệt' to this ContentFactory article",
		"message":        "Approved article content",
	})
	if res == nil || res.IsError {
		t.Fatalf("expected success with explicit approval, got: %+v", res)
	}
	if dispatched.Channel != "fb-page" {
		t.Errorf("channel = %q, want fb-page", dispatched.Channel)
	}
}

func TestMessagePost_RejectsContentNotInApprovedReply(t *testing.T) {
	tool := NewMessageTool(t.TempDir(), false)
	var dispatched bool
	tool.SetOutboundDispatcher(func(_ context.Context, msg bus.OutboundMessage) error {
		dispatched = true
		return nil
	})

	res := tool.Execute(approvedFeedPostCtx(), map[string]any{
		"action":         "post",
		"channel":        "fb-page",
		"target":         "feed",
		"forward":        true,
		"forward_reason": "User replied 'duyệt' to this ContentFactory article",
		"message":        approvedFeedPostContent + " with an unapproved suffix",
	})
	if res == nil || !res.IsError {
		t.Fatalf("expected full-content mismatch error, got: %+v", res)
	}
	if dispatched {
		t.Fatal("dispatcher must not be called for unapproved content")
	}
}

func TestMessagePost_RejectsCaseSensitiveContentMutation(t *testing.T) {
	const reviewed = "Read https://example.com/Product/ABC"
	tool := NewMessageTool(t.TempDir(), false)
	var dispatched bool
	tool.SetOutboundDispatcher(func(_ context.Context, msg bus.OutboundMessage) error {
		dispatched = true
		return nil
	})
	ctx := approvedFeedPostCtxFor(reviewed, "review-msg-case", "")

	res := tool.Execute(ctx, map[string]any{
		"action":         "post",
		"channel":        "fb-page",
		"target":         "feed",
		"forward":        true,
		"forward_reason": "User replied 'duyệt' to this ContentFactory article",
		"message":        "Read https://example.com/product/abc",
	})
	if res == nil || !res.IsError {
		t.Fatalf("expected case-sensitive content mismatch, got: %+v", res)
	}
	if dispatched {
		t.Fatal("dispatcher must not be called for case-mutated content")
	}
}

func TestMessagePost_RejectsDifferentDestination(t *testing.T) {
	tool := NewMessageTool(t.TempDir(), false)
	var dispatched bool
	tool.SetOutboundDispatcher(func(_ context.Context, msg bus.OutboundMessage) error {
		dispatched = true
		return nil
	})

	res := tool.Execute(approvedFeedPostCtx(), map[string]any{
		"action":         "post",
		"channel":        "other-page",
		"target":         "feed",
		"forward":        true,
		"forward_reason": "User replied 'duyệt' to this ContentFactory article",
		"message":        approvedFeedPostContent,
	})
	if res == nil || !res.IsError {
		t.Fatalf("expected fixed-destination rejection, got: %+v", res)
	}
	if dispatched {
		t.Fatal("dispatcher must not be called for a different destination")
	}
}

func TestMessagePost_DefaultTarget(t *testing.T) {
	tool := NewMessageTool(t.TempDir(), false)
	var dispatched bus.OutboundMessage
	tool.SetOutboundDispatcher(func(_ context.Context, msg bus.OutboundMessage) error {
		dispatched = msg
		return nil
	})

	// No target in args, no chatID in context — should default to "feed".
	res := tool.Execute(approvedFeedPostCtx(), map[string]any{
		"action":         "post",
		"channel":        "fb-page",
		"forward":        true,
		"forward_reason": "User replied 'duyệt' to this ContentFactory article",
		"message":        approvedFeedPostContent,
	})
	if res == nil || res.IsError {
		t.Fatalf("expected success, got: %+v", res)
	}
	if dispatched.ChatID != "feed" {
		t.Errorf("chatID = %q, want feed (default)", dispatched.ChatID)
	}
}

func TestMessagePost_ConcurrentApprovalDispatchesOnce(t *testing.T) {
	tool := NewMessageTool(t.TempDir(), false)
	var dispatchCalls atomic.Int32
	tool.SetOutboundDispatcher(func(_ context.Context, msg bus.OutboundMessage) error {
		dispatchCalls.Add(1)
		time.Sleep(20 * time.Millisecond)
		return nil
	})
	ctx := approvedFeedPostCtxFor(approvedFeedPostContent, "review-msg-concurrent", "")
	args := map[string]any{
		"action":         "post",
		"channel":        "fb-page",
		"target":         "feed",
		"forward":        true,
		"forward_reason": "User replied 'duyệt' to this ContentFactory article",
		"message":        approvedFeedPostContent,
	}

	const attempts = 8
	var successes atomic.Int32
	var wg sync.WaitGroup
	wg.Add(attempts)
	for range attempts {
		go func() {
			defer wg.Done()
			if res := tool.Execute(ctx, args); res != nil && !res.IsError {
				successes.Add(1)
			}
		}()
	}
	wg.Wait()

	if got := dispatchCalls.Load(); got != 1 {
		t.Fatalf("dispatcher called %d times, want 1", got)
	}
	if got := successes.Load(); got != 1 {
		t.Fatalf("successful executions = %d, want 1", got)
	}
}

func TestMessagePost_ReservationSharedAcrossUserWorkspaces(t *testing.T) {
	dataDir := t.TempDir()
	toolA := NewMessageTool(t.TempDir(), false)
	toolB := NewMessageTool(t.TempDir(), false)
	toolA.SetDataDir(dataDir)
	toolB.SetDataDir(dataDir)
	var dispatchCalls atomic.Int32
	dispatcher := func(_ context.Context, msg bus.OutboundMessage) error {
		dispatchCalls.Add(1)
		return nil
	}
	toolA.SetOutboundDispatcher(dispatcher)
	toolB.SetOutboundDispatcher(dispatcher)
	ctx := approvedFeedPostCtxFor(approvedFeedPostContent, "review-msg-cross-workspace", "")
	args := map[string]any{
		"action":         "post",
		"channel":        "fb-page",
		"target":         "feed",
		"forward":        true,
		"forward_reason": "User replied 'duyệt' to this ContentFactory article",
		"message":        approvedFeedPostContent,
	}

	if res := toolA.Execute(ctx, args); res == nil || res.IsError {
		t.Fatalf("first workspace post failed: %+v", res)
	}
	if res := toolB.Execute(ctx, args); res == nil || !res.IsError {
		t.Fatalf("second workspace should be blocked by shared reservation: %+v", res)
	}
	if got := dispatchCalls.Load(); got != 1 {
		t.Fatalf("dispatcher called %d times, want 1", got)
	}
}

func TestMessagePost_WithMedia(t *testing.T) {
	workspace := t.TempDir()
	imgFile := filepath.Join(workspace, "photo.png")
	os.WriteFile(imgFile, []byte("png-data"), 0o644)
	imgCanonical, _ := filepath.EvalSymlinks(imgFile)
	imgSHA, err := hashFeedPostFile(imgCanonical)
	if err != nil {
		t.Fatal(err)
	}

	tool := NewMessageTool(workspace, true)
	var dispatched bus.OutboundMessage
	var dispatchedMedia []byte
	tool.SetOutboundDispatcher(func(_ context.Context, msg bus.OutboundMessage) error {
		dispatched = msg
		var err error
		dispatchedMedia, err = os.ReadFile(msg.Media[0].URL)
		return err
	})

	ctx := approvedFeedPostCtxFor("Article text", "review-msg-media", "photo.png="+imgSHA)
	res := tool.Execute(ctx, map[string]any{
		"action":         "post",
		"channel":        "fb-page",
		"target":         "feed",
		"forward":        true,
		"forward_reason": "User replied 'duyệt' to this ContentFactory article",
		"message":        "Article text\nMEDIA:" + imgCanonical,
	})
	if res == nil || res.IsError {
		t.Fatalf("expected success, got: %+v", res)
	}
	if len(dispatched.Media) != 1 {
		t.Fatalf("expected 1 media, got %d", len(dispatched.Media))
	}
	if dispatched.Media[0].URL == imgCanonical {
		t.Errorf("media URL should use a gateway-owned immutable snapshot")
	}
	if string(dispatchedMedia) != "png-data" {
		t.Errorf("dispatched media = %q, want png-data", dispatchedMedia)
	}
	if _, err := os.Stat(dispatched.Media[0].URL); !os.IsNotExist(err) {
		t.Errorf("staged media should be removed after dispatch, stat err = %v", err)
	}
	if dispatched.Content != "Article text" {
		t.Errorf("content = %q, want 'Article text' (MEDIA: stripped)", dispatched.Content)
	}
	if dispatched.Metadata["approved_media_sha256"] != imgSHA {
		t.Errorf("approved_media_sha256 = %q, want %q", dispatched.Metadata["approved_media_sha256"], imgSHA)
	}
}

func TestMessagePost_RejectsMediaDigestMismatch(t *testing.T) {
	workspace := t.TempDir()
	imgFile := filepath.Join(workspace, "photo.png")
	if err := os.WriteFile(imgFile, []byte("unapproved-image"), 0o644); err != nil {
		t.Fatal(err)
	}
	approvedFile := filepath.Join(workspace, "approved.png")
	if err := os.WriteFile(approvedFile, []byte("approved-image"), 0o644); err != nil {
		t.Fatal(err)
	}
	approvedSHA, err := hashFeedPostFile(approvedFile)
	if err != nil {
		t.Fatal(err)
	}

	tool := NewMessageTool(workspace, true)
	var dispatched bool
	tool.SetOutboundDispatcher(func(_ context.Context, msg bus.OutboundMessage) error {
		dispatched = true
		return nil
	})
	ctx := approvedFeedPostCtxFor("Article text", "review-msg-media-mismatch", "approved.png="+approvedSHA)
	res := tool.Execute(ctx, map[string]any{
		"action":         "post",
		"channel":        "fb-page",
		"target":         "feed",
		"forward":        true,
		"forward_reason": "User replied 'duyệt' to this ContentFactory article",
		"message":        "Article text\nMEDIA:" + imgFile,
	})
	if res == nil || !res.IsError {
		t.Fatalf("expected media digest mismatch, got: %+v", res)
	}
	if dispatched {
		t.Fatal("dispatcher must not be called for unapproved media")
	}
}

func TestMessagePost_MalformedFeedPostMediaAborts(t *testing.T) {
	tool := NewMessageTool(t.TempDir(), true)
	var dispatched bool
	tool.SetOutboundDispatcher(func(_ context.Context, msg bus.OutboundMessage) error {
		dispatched = true
		return nil
	})

	res := tool.Execute(approvedFeedPostCtx(), map[string]any{
		"action":         "post",
		"channel":        "fb-page",
		"target":         "feed",
		"forward":        true,
		"forward_reason": "User replied 'duyệt' to this ContentFactory article",
		"message":        "Article text\nMEDIA: /tmp/photo.png",
	})
	if res == nil || !res.IsError {
		t.Fatalf("expected malformed media error, got: %+v", res)
	}
	if dispatched {
		t.Fatal("dispatcher must not receive malformed MEDIA content")
	}
}

func TestMessagePost_MixedMalformedFeedPostMediaAborts(t *testing.T) {
	workspace := t.TempDir()
	imgFile := filepath.Join(workspace, "photo.png")
	os.WriteFile(imgFile, []byte("png-data"), 0o644)
	imgCanonical, _ := filepath.EvalSymlinks(imgFile)

	tool := NewMessageTool(workspace, true)
	var dispatched bool
	tool.SetOutboundDispatcher(func(_ context.Context, msg bus.OutboundMessage) error {
		dispatched = true
		return nil
	})

	res := tool.Execute(approvedFeedPostCtx(), map[string]any{
		"action":         "post",
		"channel":        "fb-page",
		"target":         "feed",
		"forward":        true,
		"forward_reason": "User replied 'duyệt' to this ContentFactory article",
		"message":        "Article text MEDIA:" + imgCanonical + " MEDIA: /tmp/private.png",
	})
	if res == nil || !res.IsError {
		t.Fatalf("expected mixed malformed media error, got: %+v", res)
	}
	if dispatched {
		t.Fatal("dispatcher must not receive mixed malformed MEDIA content")
	}
}

func TestMessagePost_InvalidFeedPostMediaAborts(t *testing.T) {
	workspace := t.TempDir()
	outside := filepath.Join(t.TempDir(), "secret.png")
	os.WriteFile(outside, []byte("secret"), 0o644)

	tool := NewMessageTool(workspace, true)
	var dispatched bool
	tool.SetOutboundDispatcher(func(_ context.Context, msg bus.OutboundMessage) error {
		dispatched = true
		return nil
	})

	res := tool.Execute(approvedFeedPostCtx(), map[string]any{
		"action":         "post",
		"channel":        "fb-page",
		"target":         "feed",
		"forward":        true,
		"forward_reason": "User replied 'duyệt' to this ContentFactory article",
		"message":        "Article text\nMEDIA:" + outside,
	})
	if res == nil || !res.IsError {
		t.Fatalf("expected invalid media error, got: %+v", res)
	}
	if dispatched {
		t.Fatal("dispatcher must not receive unresolved MEDIA content")
	}
	if strings.Contains(res.ForLLM, outside) {
		t.Fatalf("error leaked raw media path: %q", res.ForLLM)
	}
}
