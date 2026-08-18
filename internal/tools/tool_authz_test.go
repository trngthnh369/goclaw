package tools

import (
	"context"
	"errors"
	"testing"
)

// recordingAuthorizer denies everything and records what it was asked about.
type recordingAuthorizer struct {
	seen []ToolAuthzRequest
	err  error
}

func (a *recordingAuthorizer) AuthorizeTool(_ context.Context, req ToolAuthzRequest) error {
	a.seen = append(a.seen, req)
	return a.err
}

func gatedRegistry(t *testing.T, az ToolAuthorizer, tools ...Tool) *Registry {
	t.Helper()

	r := NewRegistry()
	for _, tool := range tools {
		r.Register(tool)
	}
	r.SetToolAuthorizer(az)
	return r
}

func TestExecute_gatesMutatingTools(t *testing.T) {
	az := &recordingAuthorizer{err: errors.New("needs approval")}
	r := gatedRegistry(t, az, &capStub{name: "write_report"})

	res := r.Execute(context.Background(), "write_report", map[string]any{"body": "x"})

	if res == nil || !res.IsError {
		t.Fatalf("denied call should return an error result, got %+v", res)
	}
	if len(az.seen) != 1 {
		t.Fatalf("authorizer consulted %d times, want 1", len(az.seen))
	}
	if az.seen[0].Tool != "write_report" {
		t.Errorf("authorized tool = %q, want write_report", az.seen[0].Tool)
	}
	if got := az.seen[0].Args["body"]; got != "x" {
		t.Errorf("authorizer saw args %v, want the executed args", az.seen[0].Args)
	}
}

func TestExecute_skipsGateForReadOnlyTools(t *testing.T) {
	az := &recordingAuthorizer{err: errors.New("must not be asked")}
	r := gatedRegistry(t, az, &capStubAware{capStub{name: "peek", caps: []ToolCapability{CapReadOnly}}})

	res := r.Execute(context.Background(), "peek", nil)

	if res.IsError {
		t.Fatalf("read-only call must not be gated, got %q", res.ForLLM)
	}
	if len(az.seen) != 0 {
		t.Fatalf("authorizer consulted for a read-only tool: %+v", az.seen)
	}
}

// A tool claiming read-only *and* mutating must still be gated: IsReadOnly on
// its own would wave through something that has side effects.
func TestExecute_gatesToolClaimingBothCapabilities(t *testing.T) {
	az := &recordingAuthorizer{err: errors.New("needs approval")}
	r := gatedRegistry(t, az, &capStubAware{capStub{
		name: "sneaky",
		caps: []ToolCapability{CapReadOnly, CapMutating},
	}})

	if res := r.Execute(context.Background(), "sneaky", nil); !res.IsError {
		t.Fatal("a tool declaring both read-only and mutating must still be gated")
	}
	if len(az.seen) != 1 {
		t.Fatalf("authorizer consulted %d times, want 1", len(az.seen))
	}
}

// spawn carries only CapAsync, so an IsMutating-based gate would miss it and an
// agent could have a subagent do the mutating work instead.
func TestExecute_gatesAsyncSpawn(t *testing.T) {
	az := &recordingAuthorizer{err: errors.New("needs approval")}
	r := gatedRegistry(t, az, &capStub{name: "spawn"})

	if res := r.Execute(context.Background(), "spawn", nil); !res.IsError {
		t.Fatal("spawn must be gated")
	}
}

// Aliases must not be an escape hatch: the policy sees the canonical name.
func TestExecute_gateSeesCanonicalNameForAliases(t *testing.T) {
	az := &recordingAuthorizer{err: errors.New("needs approval")}
	r := gatedRegistry(t, az, &capStub{name: "write_report"})
	r.RegisterAlias("save_report", "write_report")

	if res := r.Execute(context.Background(), "save_report", nil); !res.IsError {
		t.Fatal("aliased call must be gated")
	}
	if len(az.seen) != 1 || az.seen[0].Tool != "write_report" {
		t.Fatalf("policy must see the canonical name, saw %+v", az.seen)
	}
}

func TestExecute_passesSystemReasonToPolicy(t *testing.T) {
	az := &recordingAuthorizer{} // allows
	r := gatedRegistry(t, az, &capStub{name: "write_report"})

	ctx := WithSystemToolCall(context.Background(), "memory flush")
	if res := r.Execute(ctx, "write_report", nil); res.IsError {
		t.Fatalf("unexpected denial: %s", res.ForLLM)
	}
	if len(az.seen) != 1 {
		t.Fatalf("authorizer consulted %d times, want 1", len(az.seen))
	}
	if az.seen[0].SystemReason != "memory flush" {
		t.Errorf("SystemReason = %q, want the reason set on ctx", az.seen[0].SystemReason)
	}
}

// The gate must not self-bypass on a system call — the policy decides.
func TestExecute_systemCallStillReachesPolicy(t *testing.T) {
	az := &recordingAuthorizer{err: errors.New("denied even for system")}
	r := gatedRegistry(t, az, &capStub{name: "write_report"})

	ctx := WithSystemToolCall(context.Background(), "memory flush")
	if res := r.Execute(ctx, "write_report", nil); !res.IsError {
		t.Fatal("a system-initiated call must still go through the policy")
	}
}

// Subagents run on a clone; losing the authorizer there would leave every tool
// call inside a child run ungated.
func TestClone_carriesTheAuthorizer(t *testing.T) {
	az := &recordingAuthorizer{err: errors.New("needs approval")}
	r := gatedRegistry(t, az, &capStub{name: "write_report"})

	if res := r.Clone().Execute(context.Background(), "write_report", nil); !res.IsError {
		t.Fatal("cloned registry must still be gated")
	}
}

func TestExecute_ungatedWithoutAuthorizer(t *testing.T) {
	r := NewRegistry()
	r.Register(&capStub{name: "write_report"})

	if res := r.Execute(context.Background(), "write_report", nil); res.IsError {
		t.Fatalf("with no policy wired the call should proceed, got %q", res.ForLLM)
	}
}
