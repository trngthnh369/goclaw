package tools

import "testing"

// TestResolveDelegateTimeoutSec pins the mode-aware timeout resolution. The critical
// regression guard is case "async omitted": it MUST stay 600s (the historical hardcoded
// 10-min budget), never the 300s parse default — otherwise every existing async delegation
// silently halves.
func TestResolveDelegateTimeoutSec(t *testing.T) {
	cases := []struct {
		name string
		mode string
		args map[string]any
		want int
	}{
		{"async omitted keeps 600 default", "async", map[string]any{}, 600},
		{"async explicit 3000 honored", "async", map[string]any{"timeout": float64(3000)}, 3000},
		{"async explicit 9000 capped at 3600", "async", map[string]any{"timeout": float64(9000)}, 3600},
		{"async explicit 300 honored (not treated as absent)", "async", map[string]any{"timeout": float64(300)}, 300},
		{"sync omitted keeps 300 default", "sync", map[string]any{}, 300},
		{"sync explicit 9000 capped at 600", "sync", map[string]any{"timeout": float64(9000)}, 600},
		{"sync explicit 120 honored", "sync", map[string]any{"timeout": float64(120)}, 120},
		{"zero timeout ignored, falls to mode default", "async", map[string]any{"timeout": float64(0)}, 600},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := resolveDelegateTimeoutSec(tc.mode, tc.args); got != tc.want {
				t.Fatalf("resolveDelegateTimeoutSec(%q, %v) = %d, want %d", tc.mode, tc.args, got, tc.want)
			}
		})
	}
}
