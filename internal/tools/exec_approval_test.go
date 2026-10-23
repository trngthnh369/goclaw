package tools

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/nextlevelbuilder/goclaw/internal/store"
)

// tenantCtx builds a caller context scoped to a single tenant (not master).
func tenantCtx(id uuid.UUID) context.Context {
	return store.WithTenantID(context.Background(), id)
}

// runCtx builds the context an agent run would carry when requesting approval.
func runCtx(tenantID uuid.UUID, sessionKey string) context.Context {
	return store.WithRunContext(context.Background(), &store.RunContext{
		TenantID:   tenantID,
		SessionKey: sessionKey,
		UserID:     "user-1",
	})
}

func askAlwaysManager() *ExecApprovalManager {
	return NewExecApprovalManager(ExecApprovalConfig{
		Security: ExecSecurityFull,
		Ask:      ExecAskAlways,
	})
}

// park starts a RequestApproval in the background and returns a channel with its
// result plus the id it registered, once the request is actually pending.
func park(t *testing.T, m *ExecApprovalManager, ctx context.Context, command string, timeout time.Duration) (<-chan ApprovalDecision, <-chan error, string) {
	t.Helper()

	decCh := make(chan ApprovalDecision, 1)
	errCh := make(chan error, 1)
	go func() {
		d, err := m.RequestApproval(ctx, command, "agent-key", timeout)
		decCh <- d
		errCh <- err
	}()

	id := waitForPending(t, m, command)
	return decCh, errCh, id
}

// waitForPending polls the manager until the given command is registered.
func waitForPending(t *testing.T, m *ExecApprovalManager, command string) string {
	t.Helper()

	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		// context.Background() has no tenant, so it is master scope and sees all.
		for _, pa := range m.ListPending(context.Background()) {
			if pa.Command == command {
				return pa.ID
			}
		}
		time.Sleep(2 * time.Millisecond)
	}
	t.Fatalf("approval for %q never became pending", command)
	return ""
}

func TestRequestApproval_isolatedAcrossTenants(t *testing.T) {
	tenantA := uuid.New()
	tenantB := uuid.New()
	m := askAlwaysManager()

	_, _, id := park(t, m, runCtx(tenantA, "sess-a"), "rm -rf /tmp/a", 2*time.Second)

	if got := m.ListPending(tenantCtx(tenantB)); len(got) != 0 {
		t.Fatalf("tenant B must not see tenant A's approvals, got %d", len(got))
	}
	if got := m.ListPending(tenantCtx(tenantA)); len(got) != 1 {
		t.Fatalf("tenant A must see its own approval, got %d", len(got))
	}

	if err := m.Resolve(tenantCtx(tenantB), id, ApprovalAllowOnce); err == nil {
		t.Fatal("tenant B must not be able to resolve tenant A's approval")
	}
	// Still pending for its owner after the cross-tenant attempt.
	if got := m.ListPending(tenantCtx(tenantA)); len(got) != 1 {
		t.Fatalf("approval must survive a rejected cross-tenant resolve, got %d", len(got))
	}

	if err := m.Resolve(tenantCtx(tenantA), id, ApprovalAllowOnce); err != nil {
		t.Fatalf("owner tenant must be able to resolve: %v", err)
	}
}

func TestRequestApproval_unscopedIsMasterOnly(t *testing.T) {
	m := askAlwaysManager()

	// No RunContext at all — nothing to attribute the request to.
	_, _, id := park(t, m, context.Background(), "apt-get install curl", 2*time.Second)

	if got := m.ListPending(tenantCtx(uuid.New())); len(got) != 0 {
		t.Fatal("an unscoped approval must be invisible to a tenant-scoped caller")
	}
	if err := m.Resolve(tenantCtx(uuid.New()), id, ApprovalAllowOnce); err == nil {
		t.Fatal("an unscoped approval must not be resolvable by a tenant-scoped caller")
	}
	if got := m.ListPending(context.Background()); len(got) != 1 {
		t.Fatal("master scope must still see the unscoped approval")
	}
}

func TestResolve_singleWinnerAgainstTimeout(t *testing.T) {
	tenant := uuid.New()
	m := askAlwaysManager()

	// Short timeout so the timeout branch is the likely winner.
	decCh, errCh, id := park(t, m, runCtx(tenant, "sess"), "sleep 1", 40*time.Millisecond)

	time.Sleep(80 * time.Millisecond) // let the timeout fire first
	resolveErr := m.Resolve(tenantCtx(tenant), id, ApprovalAllowOnce)

	decision := <-decCh
	reqErr := <-errCh

	// Exactly one side may claim the approval.
	if resolveErr == nil && reqErr == nil {
		t.Fatal("both the resolver and the requester reported success for one approval")
	}
	if resolveErr == nil && decision != ApprovalAllowOnce {
		t.Fatalf("resolver won but requester saw %q", decision)
	}
	if resolveErr != nil && decision != ApprovalDeny {
		t.Fatalf("timeout won but requester saw %q", decision)
	}
}

func TestRequestApproval_cancelledContextStopsWaiting(t *testing.T) {
	tenant := uuid.New()
	m := askAlwaysManager()

	ctx, cancel := context.WithCancel(runCtx(tenant, "sess"))
	decCh, errCh, _ := park(t, m, ctx, "npm install left-pad", time.Minute)

	cancel()

	select {
	case d := <-decCh:
		if d != ApprovalDeny {
			t.Fatalf("cancelled run must deny, got %q", d)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("cancelling the run context did not unblock RequestApproval")
	}
	if err := <-errCh; err == nil {
		t.Fatal("cancellation should surface an error")
	}
	if got := m.ListPending(context.Background()); len(got) != 0 {
		t.Fatalf("cancelled approval must not stay pending, got %d", len(got))
	}
}

func TestShutdown_drainsPendingAsDeny(t *testing.T) {
	tenant := uuid.New()
	m := askAlwaysManager()

	decCh, _, _ := park(t, m, runCtx(tenant, "sess"), "pip install requests", time.Minute)

	m.Shutdown()

	select {
	case d := <-decCh:
		if d != ApprovalDeny {
			t.Fatalf("shutdown must deny parked approvals, got %q", d)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("Shutdown did not release the parked approval")
	}

	// New requests are refused rather than parked once shutting down.
	_, err := m.RequestApproval(runCtx(tenant, "sess"), "ls", "agent-key", time.Minute)
	if !errors.Is(err, ErrApprovalUnavailable) {
		t.Fatalf("expected ErrApprovalUnavailable after shutdown, got %v", err)
	}
}

func TestRequestApproval_capsPendingPerScope(t *testing.T) {
	tenant := uuid.New()
	m := askAlwaysManager()
	ctx := runCtx(tenant, "sess")

	for i := 0; i < defaultMaxPendingPerScope; i++ {
		park(t, m, ctx, "cmd-"+string(rune('a'+i)), time.Minute)
	}

	// One past the per-tenant cap must be denied immediately, not parked:
	// a parked run holds a scheduler lane token shared with every other tenant.
	start := time.Now()
	decision, err := m.RequestApproval(ctx, "one-too-many", "agent-key", time.Minute)
	if !errors.Is(err, ErrApprovalUnavailable) {
		t.Fatalf("expected ErrApprovalUnavailable past the cap, got %v", err)
	}
	if decision != ApprovalDeny {
		t.Fatalf("expected deny past the cap, got %q", decision)
	}
	if elapsed := time.Since(start); elapsed > 2*time.Second {
		t.Fatalf("capped request should fail fast, took %s", elapsed)
	}
}

func TestApproval_grantsNoStandingAllowlistEntry(t *testing.T) {
	tenant := uuid.New()
	m := NewExecApprovalManager(ExecApprovalConfig{
		Security: ExecSecurityFull,
		Ask:      ExecAskOnMiss,
	})

	// "docker" is deliberately not in safeBins, so it needs approval.
	const cmd = "docker run hello-world"
	if got := m.CheckCommand(cmd); got != "ask" {
		t.Fatalf("precondition: expected ask, got %q", got)
	}

	_, _, id := park(t, m, runCtx(tenant, "sess"), cmd, 2*time.Second)
	if err := m.Resolve(tenantCtx(tenant), id, ApprovalAllowOnce); err != nil {
		t.Fatalf("resolve: %v", err)
	}

	// Approving once must not allowlist the binary for the next call — the old
	// allow-always store was process-global and cross-tenant.
	if got := m.CheckCommand(cmd); got != "ask" {
		t.Fatalf("approval must not create a standing allowlist entry, got %q", got)
	}
}
