package tools

import (
	"context"
	"strings"
	"testing"
	"time"
)

type stubApprovals struct {
	decision ApprovalDecision
	err      error
	calls    int
}

func (s *stubApprovals) RequestApproval(_ context.Context, _, _ string, _ time.Duration) (ApprovalDecision, error) {
	s.calls++
	return s.decision, s.err
}

func policyFor(mode PermissionMode, approvals ApprovalRequester) *ApprovalPolicy {
	return NewApprovalPolicy(func(context.Context) PermissionMode { return mode }, approvals, time.Second)
}

func mutatingReq() ToolAuthzRequest {
	return ToolAuthzRequest{Tool: "write_file", Meta: ToolMetadata{Name: "write_file"}}
}

func TestParsePermissionMode(t *testing.T) {
	for _, tc := range []struct {
		in      string
		want    PermissionMode
		wantErr bool
	}{
		{"", PermissionModeDefault, false},
		{"default", PermissionModeDefault, false},
		{"approve-mutating", PermissionModeApproveMutating, false},
		{"bypass", PermissionModeBypass, false},
		{"approve_mutating", PermissionModeDefault, true},
		{"Approve-Mutating", PermissionModeDefault, true},
		{"yolo", PermissionModeDefault, true},
	} {
		got, err := ParsePermissionMode(tc.in)
		if (err != nil) != tc.wantErr {
			t.Errorf("ParsePermissionMode(%q) err = %v, wantErr %v", tc.in, err, tc.wantErr)
		}
		if got != tc.want {
			t.Errorf("ParsePermissionMode(%q) = %q, want %q", tc.in, got, tc.want)
		}
	}
}

func TestApprovalPolicy_defaultModeDoesNotAsk(t *testing.T) {
	ap := &stubApprovals{decision: ApprovalAllowOnce}
	p := policyFor(PermissionModeDefault, ap)

	ctx := WithSurface(context.Background(), SurfaceChannelDM)
	if err := p.AuthorizeTool(ctx, mutatingReq()); err != nil {
		t.Fatalf("default mode must allow, got %v", err)
	}
	if ap.calls != 0 {
		t.Errorf("default mode asked for approval %d times", ap.calls)
	}
}

func TestApprovalPolicy_bypassAllowsWithoutAsking(t *testing.T) {
	ap := &stubApprovals{decision: ApprovalDeny}
	p := policyFor(PermissionModeBypass, ap)

	ctx := WithSurface(context.Background(), SurfaceChannelDM)
	if err := p.AuthorizeTool(ctx, mutatingReq()); err != nil {
		t.Fatalf("bypass must allow, got %v", err)
	}
	if ap.calls != 0 {
		t.Errorf("bypass asked for approval %d times", ap.calls)
	}
}

func TestApprovalPolicy_asksOnInteractiveSurfaces(t *testing.T) {
	for _, s := range []Surface{SurfaceChannelDM, SurfaceChannelGroup, SurfaceWS} {
		ap := &stubApprovals{decision: ApprovalAllowOnce}
		p := policyFor(PermissionModeApproveMutating, ap)

		if err := p.AuthorizeTool(WithSurface(context.Background(), s), mutatingReq()); err != nil {
			t.Errorf("%s: approved call should pass, got %v", s, err)
		}
		if ap.calls != 1 {
			t.Errorf("%s: approval requested %d times, want 1", s, ap.calls)
		}
	}
}

func TestApprovalPolicy_deniedApprovalBlocksTheCall(t *testing.T) {
	ap := &stubApprovals{decision: ApprovalDeny}
	p := policyFor(PermissionModeApproveMutating, ap)

	err := p.AuthorizeTool(WithSurface(context.Background(), SurfaceChannelDM), mutatingReq())
	if err == nil {
		t.Fatal("a denied approval must block the call")
	}
	if !strings.Contains(err.Error(), "write_file") {
		t.Errorf("error should name the tool, got %q", err)
	}
}

// Autonomous surfaces have nobody watching. Parking there would hold a
// scheduler lane token shared across tenants and then deny anyway, so the
// policy must refuse straight away.
func TestApprovalPolicy_deniesImmediatelyOnAutonomousSurfaces(t *testing.T) {
	for _, s := range []Surface{SurfaceCron, SurfaceHeartbeat, SurfaceSubagent, SurfaceHTTP} {
		ap := &stubApprovals{decision: ApprovalAllowOnce}
		p := policyFor(PermissionModeApproveMutating, ap)

		err := p.AuthorizeTool(WithSurface(context.Background(), s), mutatingReq())
		if err == nil {
			t.Errorf("%s: expected denial, got allow", s)
		}
		if ap.calls != 0 {
			t.Errorf("%s: must not park for approval, asked %d times", s, ap.calls)
		}
	}
}

func TestApprovalPolicy_unknownSurfaceFailsClosed(t *testing.T) {
	ap := &stubApprovals{decision: ApprovalAllowOnce}
	p := policyFor(PermissionModeApproveMutating, ap)

	// No surface set at all, and a surface this build does not know.
	for _, ctx := range []context.Context{
		context.Background(),
		WithSurface(context.Background(), Surface("from_the_future")),
	} {
		if err := p.AuthorizeTool(ctx, mutatingReq()); err == nil {
			t.Error("an unrecognised surface must be denied")
		}
		if ap.calls != 0 {
			t.Errorf("must not ask on an unrecognised surface, asked %d times", ap.calls)
		}
	}
}

// GoClaw's own bookkeeping has nobody to ask and is not model-directed.
func TestApprovalPolicy_allowsSystemInitiatedCalls(t *testing.T) {
	ap := &stubApprovals{decision: ApprovalDeny}
	p := policyFor(PermissionModeApproveMutating, ap)

	req := mutatingReq()
	req.SystemReason = "memory flush"

	if err := p.AuthorizeTool(WithSurface(context.Background(), SurfaceCron), req); err != nil {
		t.Fatalf("system-initiated call should proceed, got %v", err)
	}
	if ap.calls != 0 {
		t.Errorf("system call must not park for approval, asked %d times", ap.calls)
	}
}

func TestApprovalPolicy_unknownModeFailsClosed(t *testing.T) {
	p := policyFor(PermissionMode("something-new"), &stubApprovals{decision: ApprovalAllowOnce})

	if err := p.AuthorizeTool(WithSurface(context.Background(), SurfaceChannelDM), mutatingReq()); err == nil {
		t.Fatal("a mode this build does not understand must deny")
	}
}

func TestApprovalPolicy_deniesWhenApprovalsUnconfigured(t *testing.T) {
	p := policyFor(PermissionModeApproveMutating, nil)

	if err := p.AuthorizeTool(WithSurface(context.Background(), SurfaceChannelDM), mutatingReq()); err == nil {
		t.Fatal("approve-mutating without an approval backend must deny, not allow")
	}
}

// The approval prompt must not carry tool arguments: they can hold secrets,
// file contents, or markup imitating the prompt itself.
func TestApprovalPolicy_promptCarriesNoArguments(t *testing.T) {
	var seen string
	ap := &recordingRequester{decision: ApprovalAllowOnce, onCommand: func(c string) { seen = c }}
	p := policyFor(PermissionModeApproveMutating, ap)

	req := mutatingReq()
	req.Args = map[string]any{"path": "/etc/shadow", "content": "sk-live-SECRET"}

	if err := p.AuthorizeTool(WithSurface(context.Background(), SurfaceChannelDM), req); err != nil {
		t.Fatalf("unexpected denial: %v", err)
	}
	if strings.Contains(seen, "SECRET") || strings.Contains(seen, "/etc/shadow") {
		t.Fatalf("approval prompt leaked arguments: %q", seen)
	}
	if seen != "write_file" {
		t.Errorf("prompt = %q, want just the tool name", seen)
	}
}

type recordingRequester struct {
	decision  ApprovalDecision
	onCommand func(string)
}

func (r *recordingRequester) RequestApproval(_ context.Context, command, _ string, _ time.Duration) (ApprovalDecision, error) {
	r.onCommand(command)
	return r.decision, nil
}

func TestSurfaceForPeerKind(t *testing.T) {
	if got := SurfaceForPeerKind("group"); got != SurfaceChannelGroup {
		t.Errorf(`SurfaceForPeerKind("group") = %q, want %q`, got, SurfaceChannelGroup)
	}
	// Anything that is not explicitly a group is treated as a direct message.
	for _, pk := range []string{"direct", "", "dm", "unexpected"} {
		if got := SurfaceForPeerKind(pk); got != SurfaceChannelDM {
			t.Errorf("SurfaceForPeerKind(%q) = %q, want %q", pk, got, SurfaceChannelDM)
		}
	}
}

func TestSurface_knownAndInteractive(t *testing.T) {
	// Every surface a run can be tagged with must be known, or the policy will
	// refuse every mutating call from that entry point.
	for _, s := range []Surface{
		SurfaceChannelDM, SurfaceChannelGroup, SurfaceWS,
		SurfaceHTTP, SurfaceCron, SurfaceHeartbeat, SurfaceSubagent,
	} {
		if !s.Known() {
			t.Errorf("%q is used by a run entry point but is not a known surface", s)
		}
	}

	if SurfaceUnknown.Known() {
		t.Error("the zero value must not count as a known surface")
	}

	// Only surfaces with a person attached may be asked to approve.
	for _, s := range []Surface{SurfaceHTTP, SurfaceCron, SurfaceHeartbeat, SurfaceSubagent, SurfaceUnknown} {
		if s.Interactive() {
			t.Errorf("%q must not be treated as interactive", s)
		}
	}
	for _, s := range []Surface{SurfaceChannelDM, SurfaceChannelGroup, SurfaceWS} {
		if !s.Interactive() {
			t.Errorf("%q should be interactive", s)
		}
	}
}
