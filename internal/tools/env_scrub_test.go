package tools

import (
	"strings"
	"testing"
)

func envContains(env []string, key string) bool {
	prefix := key + "="
	for _, kv := range env {
		if strings.HasPrefix(kv, prefix) {
			return true
		}
	}
	return false
}

func TestScrubCredentialEnv_StripsStatic(t *testing.T) {
	in := []string{
		"HOME=/root",
		"GH_TOKEN=" + "secret-abc",
		"GOCLAW_GATEWAY_TOKEN=" + "goclaw-secret",
		"PATH=/usr/bin",
		"AWS_SECRET_ACCESS_KEY=" + "topsecret",
		"RAPIDAPI_KEY=" + "rapid-secret",
	}
	out := scrubCredentialEnv(in, nil)

	if envContains(out, "GH_TOKEN") {
		t.Fatalf("GH_TOKEN must be scrubbed, got: %v", out)
	}
	if envContains(out, "AWS_SECRET_ACCESS_KEY") {
		t.Fatalf("AWS_SECRET_ACCESS_KEY must be scrubbed, got: %v", out)
	}
	if envContains(out, "RAPIDAPI_KEY") {
		t.Fatalf("RAPIDAPI_KEY must be scrubbed, got: %v", out)
	}
	if envContains(out, "GOCLAW_GATEWAY_TOKEN") {
		t.Fatalf("GOCLAW_GATEWAY_TOKEN must be scrubbed, got: %v", out)
	}
	if !envContains(out, "HOME") || !envContains(out, "PATH") {
		t.Fatalf("essential vars must be preserved, got: %v", out)
	}
}

func TestScrubCredentialEnv_StripsDynamic(t *testing.T) {
	in := []string{
		"HOME=/root",
		"MY_CUSTOM_SECRET=" + "hello",
		"KEEP_ME=yes",
	}
	out := scrubCredentialEnv(in, []string{"MY_CUSTOM_SECRET"})

	if envContains(out, "MY_CUSTOM_SECRET") {
		t.Fatalf("MY_CUSTOM_SECRET must be scrubbed, got: %v", out)
	}
	if !envContains(out, "KEEP_ME") {
		t.Fatalf("KEEP_ME must be preserved, got: %v", out)
	}
}

func TestScrubCredentialEnv_PreservesEssentials(t *testing.T) {
	in := []string{
		"HOME=/root",
		"PATH=/usr/bin",
		"TERM=xterm",
		"LANG=en_US.UTF-8",
		"USER=alice",
		"TZ=UTC",
	}
	out := scrubCredentialEnv(in, nil)
	for _, k := range []string{"HOME", "PATH", "TERM", "LANG", "USER", "TZ"} {
		if !envContains(out, k) {
			t.Fatalf("expected %s preserved, got: %v", k, out)
		}
	}
}

func TestScrubCredentialEnv_PreservesUnrelated(t *testing.T) {
	in := []string{
		"FOO=bar",
		"RANDOM_APP_FLAG=1",
		"NPM_TOKEN=" + "leakme", // static deny-list → should be scrubbed
	}
	out := scrubCredentialEnv(in, nil)
	if !envContains(out, "FOO") {
		t.Fatalf("FOO must be preserved, got: %v", out)
	}
	if !envContains(out, "RANDOM_APP_FLAG") {
		t.Fatalf("RANDOM_APP_FLAG must be preserved, got: %v", out)
	}
	if envContains(out, "NPM_TOKEN") {
		t.Fatalf("NPM_TOKEN must be scrubbed (static), got: %v", out)
	}
}

// Regression: the static list missed the gateway's own secrets, so an agent
// running `python3 -c "import os; print(os.environ)"` could read the master
// encryption key and the Postgres DSN.
func TestScrubCredentialEnv_StripsGatewaySecretsByShape(t *testing.T) {
	in := []string{
		"GOCLAW_ENCRYPTION_KEY=" + "0123abcd",
		"GOCLAW_POSTGRES_DSN=" + "postgres://goclaw:" + "pw@postgres:5432/goclaw",
		"GOCLAW_GEMINI_API_KEY=" + "AIza-x",
		"POSTGRES_PASSWORD=" + "pw",
		"REMOTE_GOCLAW_TOKEN=" + "tok",
		"REMOTE_WEBMIN_PASS=" + "pw",
		"DATABASE_URL=postgres://app:" + "pw@db/app",
		"PATH=/usr/bin",
		"HOME=/app",
		"PWD=" + "/app/workspace/codex",
		"GOCLAW_WORKSPACE=/app/workspace",
		"GOCLAW_PORT=18790",
		"REMOTE_HOST=10.0.0.52",
	}
	out := scrubCredentialEnv(in, nil)

	for _, k := range []string{
		"GOCLAW_ENCRYPTION_KEY", "GOCLAW_POSTGRES_DSN", "GOCLAW_GEMINI_API_KEY",
		"POSTGRES_PASSWORD", "REMOTE_GOCLAW_TOKEN", "REMOTE_WEBMIN_PASS", "DATABASE_URL",
	} {
		if envContains(out, k) {
			t.Errorf("%s must be scrubbed, got: %v", k, out)
		}
	}
	for _, k := range []string{"PATH", "HOME", "PWD", "GOCLAW_WORKSPACE", "GOCLAW_PORT", "REMOTE_HOST"} {
		if !envContains(out, k) {
			t.Errorf("%s must be preserved, got: %v", k, out)
		}
	}
}

// Basic smoke for the JSON key extractor used to feed dynamic keys.
func TestExtractJSONTopKeys_Simple(t *testing.T) {
	keys := extractJSONTopKeys([]byte(`{"GH_TOKEN":"x","GH_CONFIG_DIR":"/tmp"}`))
	if len(keys) != 2 {
		t.Fatalf("expected 2 keys, got %d: %v", len(keys), keys)
	}
	want := map[string]bool{"GH_TOKEN": true, "GH_CONFIG_DIR": true}
	for _, k := range keys {
		if !want[k] {
			t.Fatalf("unexpected key %q", k)
		}
	}
}

func TestExtractJSONTopKeys_NestedIgnored(t *testing.T) {
	keys := extractJSONTopKeys([]byte(`{"A":{"nested":"v"},"B":"x"}`))
	if len(keys) != 2 {
		t.Fatalf("expected 2 top keys, got %d: %v", len(keys), keys)
	}
}

func TestExtractJSONTopKeys_Malformed(t *testing.T) {
	if keys := extractJSONTopKeys([]byte(`not json`)); keys != nil {
		t.Fatalf("expected nil on malformed input, got %v", keys)
	}
}
