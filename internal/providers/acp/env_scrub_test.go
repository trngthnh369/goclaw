package acp

import (
	"slices"
	"strings"
	"testing"
)

// Regression: the prefix lists had no REMOTE_ entry, so deployment-specific
// credentials reached ACP subprocesses.
func TestFilterACPEnv_ShapeCheckCoversUnlistedNames(t *testing.T) {
	in := []string{
		"PATH=/usr/bin",
		"REMOTE_WEBMIN_PASS=" + "pw",
		"REMOTE_GOCLAW_TOKEN=" + "tok",
		"REMOTE_HOST=10.0.0.52",
		"GEMINI_API_KEY=" + "gemini-key",
		"GOOGLE_API_KEY=" + "google-key",
	}
	out := filterACPEnv(in)
	has := func(key string) bool {
		return slices.ContainsFunc(out, func(kv string) bool { return strings.HasPrefix(kv, key+"=") })
	}

	for _, k := range []string{"REMOTE_WEBMIN_PASS", "REMOTE_GOCLAW_TOKEN"} {
		if has(k) {
			t.Errorf("%s must be stripped, got %v", k, out)
		}
	}
	for _, k := range []string{"PATH", "REMOTE_HOST", "GEMINI_API_KEY", "GOOGLE_API_KEY"} {
		if !has(k) {
			t.Errorf("%s must be kept, got %v", k, out)
		}
	}
}
