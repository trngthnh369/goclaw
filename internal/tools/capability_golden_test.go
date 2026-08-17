package tools

import (
	"slices"
	"testing"
)

// goldenInferredReadOnly pins the tool names that inferMetadata currently treats
// as read-only.
//
// Why this exists: no production code calls RegisterWithMetadata today, so every
// tool resolves through inferMetadata, and that classification is consumed well
// beyond any approval gate — parallel tool execution eligibility
// (internal/agent/loop.go) and capability deny rules (policy.go filterByCapability)
// both read it. Adding CapReadOnly to a tool therefore makes it parallel-eligible
// for the first time and changes what deny_capabilities resolves to for existing
// agents. This golden set turns that into a visible, reviewed diff instead of a
// silent behaviour change.
var goldenInferredReadOnly = []string{
	"datetime",
	"knowledge_graph_search",
	"list_files",
	"memory_expand",
	"memory_get",
	"memory_search",
	"read_audio",
	"read_document",
	"read_file",
	"read_image",
	"read_video",
	"session_status",
	"sessions_history",
	"sessions_list",
	"skill_search",
	"wait",
	"web_fetch",
	"web_search",
}

func TestInferMetadata_readOnlySetIsGolden(t *testing.T) {
	for _, name := range goldenInferredReadOnly {
		meta := inferMetadata(name)
		if !meta.IsReadOnly() {
			t.Errorf("%s: expected read-only, got %v", name, meta.Capabilities)
		}
	}

	// Anything gaining read-only outside the golden list must be deliberate.
	extra := []string{
		"exec", "write_file", "edit", "message", "cron", "browser",
		"create_image", "tts", "spawn", "delegate", "team_tasks",
		"memory_write", "skill_manage", "publish_skill",
	}
	for _, name := range extra {
		if inferMetadata(name).IsReadOnly() {
			t.Errorf("%s unexpectedly infers as read-only; update goldenInferredReadOnly deliberately if intended", name)
		}
	}
}

func TestInferMetadata_unknownToolFailsClosed(t *testing.T) {
	meta := inferMetadata("some_tool_added_next_week")
	if meta.IsReadOnly() {
		t.Fatal("an unknown tool must not infer as read-only")
	}
	if !meta.IsMutating() {
		t.Fatalf("an unknown tool must default to mutating, got %v", meta.Capabilities)
	}
}

// spawn is the specific escape hatch that motivated gating on !IsReadOnly()
// rather than IsMutating(): it is neither, so an IsMutating()-based gate lets an
// agent spawn a subagent and have the child do the mutating work ungated.
func TestInferMetadata_spawnIsNeitherReadOnlyNorMutating(t *testing.T) {
	meta := inferMetadata("spawn")

	if meta.IsMutating() {
		t.Fatal("precondition changed: spawn now reports mutating, revisit the gate predicate")
	}
	if meta.IsReadOnly() {
		t.Fatal("spawn must never be read-only — a !IsReadOnly() gate has to catch it")
	}
	if !meta.HasCapability(CapAsync) {
		t.Fatalf("expected spawn to be async, got %v", meta.Capabilities)
	}
}

// A tool claiming both read-only and mutating would slip through a
// !IsReadOnly() gate while still having side effects. Nothing declares both
// today; this pins that, because the approval gate depends on it.
func TestInferMetadata_neverDeclaresReadOnlyAndMutating(t *testing.T) {
	names := slices.Clone(goldenInferredReadOnly)
	names = append(names,
		"exec", "write_file", "edit", "spawn", "message", "cron",
		"browser", "team_tasks", "some_unknown_tool",
	)

	for _, name := range names {
		meta := inferMetadata(name)
		if meta.HasCapability(CapReadOnly) && meta.HasCapability(CapMutating) {
			t.Errorf("%s declares both read-only and mutating; IsReadOnly() would report true for a tool with side effects", name)
		}
	}
}
