package providers

import (
	"slices"
	"strings"
	"testing"
)

func envHasKey(env []string, key string) bool {
	return slices.ContainsFunc(env, func(kv string) bool { return strings.HasPrefix(kv, key+"=") })
}

// Regression: filterCLIEnv only removed CLAUDE* names, so the claude CLI (which
// runs its own shell tools) inherited the gateway's master key and DSN.
func TestFilterCLIEnv_WithholdsGatewaySecretsKeepsCLIAuth(t *testing.T) {
	in := []string{
		"PATH=/usr/bin",
		"HOME=/app",
		"GOCLAW_ENCRYPTION_KEY=" + "0123abcd",
		"GOCLAW_POSTGRES_DSN=" + "postgres://goclaw:" + "pw@postgres/goclaw",
		"REMOTE_WEBMIN_PASS=" + "pw",
		"CLAUDE_CODE_OAUTH_TOKEN=" + "oauth",
		"ANTHROPIC_API_KEY=" + "api",
		"CLAUDE_CODE_EFFORT_LEVEL=high",
		"CLAUDECODE=1",
	}
	out := filterCLIEnv(in)

	for _, k := range []string{"GOCLAW_ENCRYPTION_KEY", "GOCLAW_POSTGRES_DSN", "REMOTE_WEBMIN_PASS", "CLAUDECODE"} {
		if envHasKey(out, k) {
			t.Errorf("%s must be withheld from the claude CLI, got %v", k, out)
		}
	}
	for _, k := range []string{"PATH", "HOME", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "CLAUDE_CODE_EFFORT_LEVEL"} {
		if !envHasKey(out, k) {
			t.Errorf("%s must reach the claude CLI, got %v", k, out)
		}
	}
}
