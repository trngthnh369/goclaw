package tools

import (
	"testing"

	"github.com/nextlevelbuilder/goclaw/internal/config"
	"github.com/nextlevelbuilder/goclaw/internal/providers"
)

// The composed engine is what actually authorizes a tool call — the agent-side
// helper that builds the policy is only half the story. These tests pin the
// resolution order for the shape that matters most: a publication gate agent
// that is a team member, whose file tools must not be reachable.
//
// Order in evaluate(): profile → allow intersect → global deny → agent deny →
// alsoAllow union. alsoAllow last is why an injected file tool used to win.

func filteredNames(defs []providers.ToolDefinition) map[string]bool {
	out := make(map[string]bool, len(defs))
	for _, d := range defs {
		out[d.Function.Name] = true
	}
	return out
}

func TestPolicyEngine_AuditorPolicyKeepsFileToolsOut(t *testing.T) {
	reg := NewRegistry()
	for _, name := range []string{"web_search", "web_fetch", "memory_search", "memory_get", "read_file", "write_file", "list_files"} {
		reg.Register(&mockTool{name: name})
	}
	pe := NewPolicyEngine(&config.ToolsConfig{})

	// Exactly the cf-auditor shape after the 2026-08-05 config change:
	// a narrow allow list plus an explicit deny, on an agent in a team.
	policy := &config.ToolPolicySpec{
		Allow: []string{"web_search", "web_fetch", "memory_search", "memory_get"},
		Deny:  []string{"read_file", "write_file", "list_files"},
	}
	// Simulates what the agent resolver injects for a team member.
	policy.AlsoAllow = append(policy.AlsoAllow, teamWorkspaceInjection(policy)...)

	got := filteredNames(pe.FilterTools(reg, "auditor", "anthropic", policy, nil, false, false))

	for _, blocked := range []string{"read_file", "write_file", "list_files"} {
		if got[blocked] {
			t.Errorf("%s reached the auditor: the publication gate must not touch the team workspace", blocked)
		}
	}
	for _, allowed := range []string{"web_search", "web_fetch", "memory_search", "memory_get"} {
		if !got[allowed] {
			t.Errorf("%s should remain available to the auditor", allowed)
		}
	}
}

func TestPolicyEngine_DenyListedFileToolStaysOutForTeamMember(t *testing.T) {
	reg := NewRegistry()
	for _, name := range []string{"read_file", "write_file", "list_files", "web_search"} {
		reg.Register(&mockTool{name: name})
	}
	pe := NewPolicyEngine(&config.ToolsConfig{})

	// cf-designer shape: deny-only policy, no allow list.
	policy := &config.ToolPolicySpec{Deny: []string{"list_files"}}
	policy.AlsoAllow = append(policy.AlsoAllow, teamWorkspaceInjection(policy)...)

	got := filteredNames(pe.FilterTools(reg, "designer", "anthropic", policy, nil, false, false))

	if got["list_files"] {
		t.Error("list_files is denied but still reachable — this is the loop that stalled cf-designer")
	}
	for _, allowed := range []string{"read_file", "write_file"} {
		if !got[allowed] {
			t.Errorf("%s is not denied and must stay available to a team member", allowed)
		}
	}
}

// teamWorkspaceInjection mirrors agent.agentToolPolicyWithWorkspace. It is
// duplicated here rather than imported because internal/agent depends on
// internal/tools, not the other way round.
func teamWorkspaceInjection(policy *config.ToolPolicySpec) []string {
	var out []string
	for _, tool := range []string{"read_file", "write_file", "list_files"} {
		denied := false
		for _, d := range policy.Deny {
			if d == tool {
				denied = true
				break
			}
		}
		if !denied {
			out = append(out, tool)
		}
	}
	return out
}
