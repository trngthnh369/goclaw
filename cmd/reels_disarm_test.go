package cmd

import (
	"strings"
	"testing"
)

func TestDisarmReelsDraftContent(t *testing.T) {
	master := "master_sha256: " + strings.Repeat("a", 64)
	armed := "🎬 Video\n[caption]\nMẹo du lịch\n[/caption]\n" + master
	cases := map[string]struct {
		in        string
		wantArmed bool
	}{
		"master-mode draft":           {armed, true},
		"legacy draft (no master)":    {"🎬 Video\n[caption]\nMẹo\n[/caption]\nMEDIA:/tmp/p.mp4", false},
		"already disarmed":            {strings.NewReplacer("[caption]", "(caption)", "[/caption]", "(/caption)").Replace(armed), false},
		"plain bot message":           {"OK, đang làm video", false},
		"master line without caption": {"note\n" + master, false},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			out, isArmed := disarmReelsDraftContent(tc.in)
			if isArmed != tc.wantArmed {
				t.Fatalf("armed = %v, want %v", isArmed, tc.wantArmed)
			}
			if !isArmed {
				if out != tc.in {
					t.Fatal("a message that is not an armed draft must be returned unchanged")
				}
				return
			}
			if len(out) != len(tc.in) {
				t.Fatalf("disarming changed the length %d -> %d; it must never grow the message", len(tc.in), len(out))
			}
			if _, again := disarmReelsDraftContent(out); again {
				t.Fatal("the disarmed message still parses as an armed draft")
			}
			if strings.Contains(out, "\n[caption]\n") || strings.Contains(out, "\n[/caption]\n") {
				t.Fatalf("caption markers survived: %q", out)
			}
		})
	}
}
