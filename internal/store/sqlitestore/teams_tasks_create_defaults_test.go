//go:build sqlite || sqliteonly

package sqlitestore

import (
	"context"
	"testing"

	"github.com/google/uuid"

	"github.com/nextlevelbuilder/goclaw/internal/store"
)

// A task created without task_role / execution_mode / idempotency_key must be
// stored with the column defaults. Passing NULL for these NOT NULL DEFAULT ''
// columns made every team_tasks create fail after the managed-run migration.
func TestCreateTask_EmptyManagedRunFields_UsesDefaults(t *testing.T) {
	db, tenantID, agentID := newAgentUpdateTestFixture(t)
	teamID := uuid.Must(uuid.NewV7())
	mustExec(t, db,
		`INSERT INTO agent_teams (id, name, lead_agent_id, created_by, tenant_id) VALUES (?, 'crew', ?, 'owner', ?)`,
		teamID.String(), agentID.String(), tenantID.String())

	ts := NewSQLiteTeamStore(db)
	ctx := store.WithTenantID(context.Background(), tenantID)
	task := &store.TeamTaskData{TeamID: teamID, Subject: "plain task", Status: store.TeamTaskStatusPending}

	if err := ts.CreateTask(ctx, task); err != nil {
		t.Fatalf("CreateTask: %v", err)
	}

	got, err := ts.GetTask(ctx, task.ID)
	if err != nil {
		t.Fatalf("GetTask: %v", err)
	}
	if got.TaskRole != "" || got.ExecutionMode != "" || got.IdempotencyKey != "" {
		t.Fatalf("want empty defaults, got role=%q mode=%q key=%q", got.TaskRole, got.ExecutionMode, got.IdempotencyKey)
	}
	if got.DependencyPolicy != store.DependencyPolicyTerminal {
		t.Fatalf("dependency_policy = %q, want %q", got.DependencyPolicy, store.DependencyPolicyTerminal)
	}
}
