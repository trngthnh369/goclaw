package security

import (
	"net/url"
	"regexp"
	"strings"
)

// sensitiveEnvTokens are underscore-separated name segments that mark an
// environment variable as a credential. Matching on whole segments (not
// substrings) keeps PWD, KEYBOARD_LAYOUT or PASSTHROUGH_MODE out of the net.
var sensitiveEnvTokens = map[string]struct{}{
	"KEY":         {},
	"KEYS":        {},
	"APIKEY":      {},
	"TOKEN":       {},
	"TOKENS":      {},
	"SECRET":      {},
	"SECRETS":     {},
	"PASS":        {},
	"PASSWORD":    {},
	"PASSWD":      {},
	"PASSPHRASE":  {},
	"DSN":         {},
	"CREDENTIAL":  {},
	"CREDENTIALS": {},
	"PRIVATE":     {},
	"AUTH":        {},
	"COOKIE":      {},
}

// sensitiveEnvSuffixes catch segments that glue a prefix onto the credential
// word, e.g. DBPASSWORD or OPENAIAPIKEY.
var sensitiveEnvSuffixes = []string{"PASSWORD", "PASSWD", "SECRET", "TOKEN", "APIKEY"}

// IsSensitiveEnv reports whether an environment variable must be withheld
// from subprocesses whose code the gateway does not control (agent shell
// commands, third-party package installs).
//
// A static deny-list alone missed GOCLAW_ENCRYPTION_KEY, GOCLAW_POSTGRES_DSN
// and deployment-specific names like REMOTE_WEBMIN_PASS, so the check is
// structural: a credential-shaped name, or a value that embeds a password.
func IsSensitiveEnv(key, value string) bool {
	for seg := range strings.SplitSeq(strings.ToUpper(key), "_") {
		if _, ok := sensitiveEnvTokens[seg]; ok {
			return true
		}
		for _, suffix := range sensitiveEnvSuffixes {
			if len(seg) > len(suffix) && strings.HasSuffix(seg, suffix) {
				return true
			}
		}
	}
	return valueEmbedsPassword(value)
}

// StripSensitiveEnv returns env without the KEY=VALUE pairs IsSensitiveEnv
// flags, except for the keys in keep (credentials a specific child process is
// meant to receive, e.g. a package registry token). keep may be nil.
func StripSensitiveEnv(env []string, keep map[string]struct{}) []string {
	out := make([]string, 0, len(env))
	for _, kv := range env {
		key, value, _ := strings.Cut(kv, "=")
		if _, ok := keep[key]; !ok && IsSensitiveEnv(key, value) {
			continue
		}
		out = append(out, kv)
	}
	return out
}

// schemelessDSN matches driver-style DSNs with no scheme, where the password
// sits before the host: user:pass@tcp(host:3306)/db, user:pass@host:5432/db.
// The whole value must have that shape, so "git@github.com:org/repo" (no
// password before the @) does not match.
var schemelessDSN = regexp.MustCompile(`^[A-Za-z0-9._%+-]+:[^\s@/]+@[^\s@]+$`)

// credentialQueryParams are query keys whose value is itself a credential,
// as in https://api.example.com/hook?token=...
var credentialQueryParams = []string{
	"token", "access_token", "api_key", "apikey", "key", "secret",
	"password", "sig", "signature", "auth",
}

// valueEmbedsPassword catches credentials stored under innocent names
// (DATABASE_URL, ALERT_WEBHOOK_URL): URL userinfo with a password, a libpq
// key/value DSN carrying password=, a scheme-less driver DSN, an incoming
// webhook URL whose path is the secret, or a credential query parameter.
func valueEmbedsPassword(value string) bool {
	if strings.Contains(strings.ToLower(value), "password=") || schemelessDSN.MatchString(value) {
		return true
	}
	if !strings.Contains(value, "://") {
		return false
	}
	u, err := url.Parse(value)
	if err != nil {
		return false
	}
	if u.User != nil {
		if _, hasPassword := u.User.Password(); hasPassword {
			return true
		}
	}
	if isSecretPathWebhook(u) {
		return true
	}
	query := u.Query()
	for _, name := range credentialQueryParams {
		if query.Get(name) != "" {
			return true
		}
	}
	return false
}

// isSecretPathWebhook reports incoming-webhook URLs whose path carries the
// token: anyone holding the URL can post as the integration.
func isSecretPathWebhook(u *url.URL) bool {
	host := strings.ToLower(u.Hostname())
	switch {
	case host == "hooks.slack.com":
		return strings.HasPrefix(u.Path, "/services/")
	case host == "discord.com" || host == "discordapp.com" ||
		strings.HasSuffix(host, ".discord.com"):
		return strings.HasPrefix(u.Path, "/api/webhooks/")
	}
	return false
}
