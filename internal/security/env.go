package security

import (
	"net/url"
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

// valueEmbedsPassword catches connection strings stored under innocent names
// (DATABASE_URL, REDIS_URL): URL userinfo with a password, or a libpq-style
// key/value DSN carrying password=.
func valueEmbedsPassword(value string) bool {
	if strings.Contains(value, "://") {
		if u, err := url.Parse(value); err == nil && u.User != nil {
			if _, hasPassword := u.User.Password(); hasPassword {
				return true
			}
		}
	}
	return strings.Contains(strings.ToLower(value), "password=")
}
