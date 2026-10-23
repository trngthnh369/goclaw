package cmd

import (
	"strings"
	"testing"

	"github.com/nextlevelbuilder/goclaw/internal/channels"
	"github.com/nextlevelbuilder/goclaw/internal/i18n"
)

// TestIsExternalChannel ensures the whitelist matches actual channel type
// constants — a mismatch here silently leaks internal error strings to end
// users (e.g. "zalo" vs "zalo_oa"/"zalo_personal"). Always compare against
// channels.Type* constants, never string literals.
func TestIsExternalChannel(t *testing.T) {
	t.Parallel()

	cases := []struct {
		name        string
		channelType string
		want        bool
	}{
		// Public-facing platforms — errors must be suppressed.
		{"facebook", channels.TypeFacebook, true},
		{"telegram", channels.TypeTelegram, true},
		{"discord", channels.TypeDiscord, true},
		{"feishu", channels.TypeFeishu, true},
		{"whatsapp", channels.TypeWhatsApp, true},
		{"zalo_oa", channels.TypeZaloOA, true},
		{"zalo_personal", channels.TypeZaloPersonal, true},
		{"pancake", channels.TypePancake, true},
		{"slack", channels.TypeSlack, true},

		// Internal / unknown channel types — errors must still surface.
		{"ws", "ws", false},
		{"empty", "", false},
		{"unknown", "myplatform", false},
		// "line" is a Pancake sub-platform, not a top-level channel type.
		{"line_not_top_level", "line", false},
		// Legacy short "zalo" string must NOT match — real constants are zalo_oa / zalo_personal.
		{"zalo_short_form", "zalo", false},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := isExternalChannel(tc.channelType); got != tc.want {
				t.Fatalf("isExternalChannel(%q) = %v, want %v", tc.channelType, got, tc.want)
			}
		})
	}
}

func TestExternalChannelErrorContent(t *testing.T) {
	t.Parallel()

	if got := externalChannelErrorContent(""); got != "" {
		t.Fatalf("no opt-in must stay silent, got %q", got)
	}
	vi := externalChannelErrorContent("vi")
	if vi == "" || vi == i18n.MsgRunFailedNotice {
		t.Fatalf("vi notice missing from catalog, got %q", vi)
	}
	if en := externalChannelErrorContent("en"); en == vi {
		t.Fatalf("en and vi notices should differ, both %q", en)
	}
	raw := "all 3 failover candidates exhausted: [1] antigravity/ag-pro: timeout"
	if strings.Contains(vi, "antigravity") || strings.Contains(vi, raw) {
		t.Fatalf("notice must not carry provider details: %q", vi)
	}
}
