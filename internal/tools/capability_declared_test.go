package tools

import (
	"context"
	"slices"
	"testing"
)

// declaredReadOnlyTools are the tools that declare CapReadOnly for themselves
// rather than inheriting it from inferMetadata's name list.
//
// Each entry is a deliberate decision with two consequences, so adding one is a
// reviewed change, not a formality:
//   - it becomes eligible for parallel tool execution for the first time
//     (agent.parallelEligibleToolCall requires read-only and not
//     mutating/async/mcp-bridged), so the tool must be safe to run concurrently;
//   - once the approval gate lands, it stops prompting a human, so the tool must
//     genuinely change nothing.
var declaredReadOnlyTools = []string{
	"list_group_members",
	"use_skill",
	"vault_read",
	"vault_search",
}

// capStub is a minimal tool used to exercise the registry wiring.
type capStub struct {
	name string
	caps []ToolCapability
}

func (s *capStub) Name() string               { return s.name }
func (s *capStub) Description() string        { return "stub" }
func (s *capStub) Parameters() map[string]any { return map[string]any{} }
func (s *capStub) Execute(context.Context, map[string]any) *Result {
	return NewResult("ok")
}

type capStubAware struct{ capStub }

func (s *capStubAware) Capabilities() []ToolCapability { return s.caps }

func TestRegister_honoursDeclaredCapabilities(t *testing.T) {
	r := NewRegistry()
	// "write_report" would infer as mutating; declaring read-only must win.
	r.Register(&capStubAware{capStub{name: "write_report", caps: []ToolCapability{CapReadOnly}}})

	meta := r.GetMetadata("write_report")
	if !meta.IsReadOnly() {
		t.Fatalf("declared capabilities ignored, got %v", meta.Capabilities)
	}
	if meta.IsMutating() {
		t.Fatalf("declared read-only tool must not also be mutating, got %v", meta.Capabilities)
	}
	if meta.Name != "write_report" {
		t.Errorf("metadata name = %q, want write_report", meta.Name)
	}
}

func TestRegister_fallsBackToInferenceWithoutDeclaration(t *testing.T) {
	r := NewRegistry()
	r.Register(&capStub{name: "write_report"})

	meta := r.GetMetadata("write_report")
	if !meta.IsMutating() {
		t.Fatalf("undeclared tool must keep the inferred mutating default, got %v", meta.Capabilities)
	}
}

// Clone copies its fields by struct literal, so anything new has to be added
// there by hand. Subagents run on a cloned registry, so a dropped declaration
// would silently reclassify tools inside a child run.
func TestClone_preservesDeclaredCapabilities(t *testing.T) {
	r := NewRegistry()
	r.Register(&capStubAware{capStub{name: "write_report", caps: []ToolCapability{CapReadOnly}}})

	if meta := r.Clone().GetMetadata("write_report"); !meta.IsReadOnly() {
		t.Fatalf("clone lost the declared capabilities, got %v", meta.Capabilities)
	}
}

// Mirrors agent.parallelEligibleToolCall for the declared set: these tools now
// run concurrently with each other, so the declaration must not carry a
// mutating/async/bridged capability alongside read-only.
func TestDeclaredReadOnlyTools_areSafeToRunInParallel(t *testing.T) {
	for _, name := range declaredReadOnlyTools {
		meta := ToolMetadata{Name: name, Capabilities: []ToolCapability{CapReadOnly}}

		eligible := meta.IsReadOnly() &&
			!meta.HasCapability(CapMutating) &&
			!meta.HasCapability(CapAsync) &&
			!meta.HasCapability(CapMCPBridged)
		if !eligible {
			t.Errorf("%s: declared capabilities are not parallel-eligible: %v", name, meta.Capabilities)
		}
	}
}

// The declared set must stay disjoint from the inferred one: a name in both is a
// sign inferMetadata changed underneath and the declaration is now redundant.
func TestDeclaredReadOnlyTools_doNotOverlapInferredSet(t *testing.T) {
	for _, name := range declaredReadOnlyTools {
		if slices.Contains(goldenInferredReadOnly, name) {
			t.Errorf("%s is both declared and inferred read-only; drop one", name)
		}
	}
}
