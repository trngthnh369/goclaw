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
