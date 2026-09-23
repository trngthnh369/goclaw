package security

import "testing"

func TestIsSensitiveEnv_CredentialNames(t *testing.T) {
	// Names taken from a real gateway container: all of these reached agent
	// exec children before the structural check existed.
	for _, key := range []string{
		"GOCLAW_ENCRYPTION_KEY",
		"GOCLAW_POSTGRES_DSN",
		"GOCLAW_GEMINI_API_KEY",
		"GOCLAW_GATEWAY_TOKEN",
		"POSTGRES_PASSWORD",
		"REMOTE_GOCLAW_TOKEN",
		"REMOTE_WEBMIN_PASS",
		"AWS_SECRET_ACCESS_KEY",
		"DOCKER_AUTH",
		"MYSQL_ROOT_PASSWD",
		"DBPASSWORD",
		"OPENAIAPIKEY",
		"ssh_private_key",
	} {
		if !IsSensitiveEnv(key, "x") {
			t.Errorf("IsSensitiveEnv(%q) = false, want true", key)
		}
	}
}

func TestIsSensitiveEnv_OrdinaryNames(t *testing.T) {
	for _, key := range []string{
		"PATH", "HOME", "PWD", "LANG", "TZ", "USER", "HOSTNAME", "SHLVL",
		"GOCLAW_WORKSPACE", "GOCLAW_PORT", "GOCLAW_OWNER_IDS", "REMOTE_HOST",
		"REMOTE_WEBMIN_USER", "PYTHONPATH", "NODE_PATH", "KEYBOARD_LAYOUT",
		"PASSTHROUGH_MODE", "TOKENIZERS_PARALLELISM",
	} {
		if IsSensitiveEnv(key, "plain-value") {
			t.Errorf("IsSensitiveEnv(%q) = true, want false", key)
		}
	}
}

func TestIsSensitiveEnv_ValueEmbedsPassword(t *testing.T) {
	cases := []struct {
		value string
		want  bool
	}{
		{"postgres://goclaw:" + "s3cret@postgres:5432/goclaw?sslmode=disable", true},
		{"redis://:" + "onlypass@redis:6379/0", true},
		{"host=db user=app password=" + "s3cret dbname=app", true},
		{"https://example.com/path", false},
		{"https://user@example.com/", false},
		{"/app/data/config.json", false},
	}
	for _, tc := range cases {
		if got := IsSensitiveEnv("DATABASE_URL", tc.value); got != tc.want {
			t.Errorf("IsSensitiveEnv(DATABASE_URL, %q) = %v, want %v", tc.value, got, tc.want)
		}
	}
}

func TestStripSensitiveEnv_KeepsAllowlistedCredential(t *testing.T) {
	in := []string{
		"PATH=/usr/bin",
		"GOCLAW_ENCRYPTION_KEY=" + "0123abcd",
		"NPM_TOKEN=" + "registry-token",
	}
	out := StripSensitiveEnv(in, map[string]struct{}{"NPM_TOKEN": {}})
	want := []string{"PATH=/usr/bin", "NPM_TOKEN=" + "registry-token"}
	if len(out) != len(want) || out[0] != want[0] || out[1] != want[1] {
		t.Fatalf("StripSensitiveEnv = %v, want %v", out, want)
	}
}

// Decision 2026-09-23: SSH_AUTH_SOCK is withheld on purpose. It is only a socket
// path, but it lets a child sign with every key loaded in the host ssh-agent;
// git over SSH from agents goes through secure-CLI credentials instead.
func TestIsSensitiveEnv_SSHAgentSocketIsWithheld(t *testing.T) {
	if !IsSensitiveEnv("SSH_AUTH_SOCK", "/tmp/ssh-XXXX/agent.1") {
		t.Fatal("SSH_AUTH_SOCK must be withheld from untrusted subprocesses")
	}
}

func TestIsSensitiveEnv_WebhookAndSchemelessValues(t *testing.T) {
	cases := []struct {
		value string
		want  bool
	}{
		{"app:" + "s3cret@tcp(db:3306)/app?parseTime=true", true},
		{"app:" + "s3cret@db:5432/app", true},
		{"https://discord.com/api/webhooks/123/" + "abcDEF-token", true},
		{"https://hooks.slack.com/services/T0/B0/" + "xyz", true},
		{"https://api.example.com/hook?token=" + "abc123", true},
		{"https://maps.example.com/v1?key=" + "AIza-x&q=hanoi", true},
		{"git@github.com:org/repo.git", false},
		{"https://discord.com/channels/1487758328577921066/1552179009540857876", false},
		{"https://example.com/search?q=hanoi&page=2", false},
		{"10.0.0.52:10000", false},
		{"Asia/Ho_Chi_Minh", false},
	}
	for _, tc := range cases {
		if got := IsSensitiveEnv("ALERT_URL", tc.value); got != tc.want {
			t.Errorf("IsSensitiveEnv(ALERT_URL, %q) = %v, want %v", tc.value, got, tc.want)
		}
	}
}
