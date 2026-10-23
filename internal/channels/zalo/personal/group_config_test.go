package personal

import (
	"testing"

	"github.com/nextlevelbuilder/goclaw/internal/config"
)

func boolPtr(b bool) *bool { return &b }

func TestResolveGroupConfig_NoGroups(t *testing.T) {
	cfg := config.ZaloPersonalConfig{
		GroupPolicy: "allowlist",
		AllowFrom:   []string{"111", "222"},
	}
	rc := resolveGroupConfig(cfg, "999")
	if rc.groupPolicy != "allowlist" {
		t.Errorf("groupPolicy = %q, want allowlist", rc.groupPolicy)
	}
	if rc.perGroupAllow {
		t.Error("perGroupAllow should be false without group config")
	}
	if !rc.enabled {
		t.Error("enabled should default true")
	}
}

func TestResolveGroupConfig_SpecificGroupOverride(t *testing.T) {
	cfg := config.ZaloPersonalConfig{
		GroupPolicy: "open",
		Groups: map[string]*config.ZaloGroupConfig{
			"g1": {
				GroupPolicy: "allowlist",
				AllowFrom:   []string{"user-a"},
			},
		},
	}

	rc := resolveGroupConfig(cfg, "g1")
	if rc.groupPolicy != "allowlist" {
		t.Errorf("g1 groupPolicy = %q, want allowlist", rc.groupPolicy)
	}
	if !rc.perGroupAllow {
		t.Error("g1 perGroupAllow should be true")
	}
	if !rc.senderInAllowFrom("user-a") {
		t.Error("user-a should be in g1 allowFrom")
	}
	if rc.senderInAllowFrom("user-b") {
		t.Error("user-b should NOT be in g1 allowFrom")
	}

	// Other groups keep the global policy.
	rcOther := resolveGroupConfig(cfg, "g2")
	if rcOther.groupPolicy != "open" {
		t.Errorf("g2 groupPolicy = %q, want open (inherit)", rcOther.groupPolicy)
	}
	if rcOther.perGroupAllow {
		t.Error("g2 perGroupAllow should be false")
	}
}

func TestResolveGroupConfig_WildcardThenSpecific(t *testing.T) {
	cfg := config.ZaloPersonalConfig{
		GroupPolicy: "open",
		Groups: map[string]*config.ZaloGroupConfig{
			"*":  {GroupPolicy: "disabled"},
			"g1": {GroupPolicy: "allowlist", AllowFrom: []string{"user-a"}},
		},
	}

	// g1: specific overrides wildcard.
	rc := resolveGroupConfig(cfg, "g1")
	if rc.groupPolicy != "allowlist" {
		t.Errorf("g1 groupPolicy = %q, want allowlist", rc.groupPolicy)
	}

	// Unlisted group: wildcard applies → disabled.
	rcOther := resolveGroupConfig(cfg, "g2")
	if rcOther.groupPolicy != "disabled" {
		t.Errorf("g2 groupPolicy = %q, want disabled (wildcard)", rcOther.groupPolicy)
	}
}

func TestResolveGroupConfig_EnabledAndMention(t *testing.T) {
	cfg := config.ZaloPersonalConfig{
		RequireMention: boolPtr(true),
		Groups: map[string]*config.ZaloGroupConfig{
			"g1": {Enabled: boolPtr(false)},
			"g2": {RequireMention: boolPtr(false)},
		},
	}

	if rc := resolveGroupConfig(cfg, "g1"); rc.enabled {
		t.Error("g1 should be disabled")
	}
	if rc := resolveGroupConfig(cfg, "g2"); rc.requireMentionOrDefault() {
		t.Error("g2 requireMention should be false (override)")
	}
	if rc := resolveGroupConfig(cfg, "g3"); !rc.requireMentionOrDefault() {
		t.Error("g3 requireMention should inherit true")
	}
}
