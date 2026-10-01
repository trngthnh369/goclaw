//go:build sqlite || sqliteonly

package sqlitestore

import (
	"context"
	"testing"

	"github.com/google/uuid"

	"github.com/nextlevelbuilder/goclaw/internal/store"
)

// The origin sender's display name only renders the announce prompt, so it is
// dropped from the task row once the task ends; the sender ID stays (#915).
func TestTerminalStatus_ClearsOriginSenderName(t *testing.T) {
	db, tenantID, agentID := newAgentUpdateTestFixture(t)
	teamID := uuid.Must(uuid.NewV7())
	mustExec(t, db,
		`INSERT INTO agent_teams (id, name, lead_agent_id, created_by, tenant_id) VALUES (?, 'crew', ?, 'owner', ?)`,
		teamID.String(), agentID.String(), tenantID.String())
	ts := NewSQLiteTeamStore(db)
	ctx := store.WithTenantID(context.Background(), tenantID)

	end := map[string]func(id uuid.UUID) error{
		"cancel":       func(id uuid.UUID) error { return ts.CancelTask(ctx, id, teamID, "stop") },
		"fail pending": func(id uuid.UUID) error { return ts.FailPendingTask(ctx, id, teamID, "invalid") },
	}
	for name, finish := range end {
		t.Run(name, func(t *testing.T) {
			task := &store.TeamTaskData{TeamID: teamID, Subject: "named " + name, Status: store.TeamTaskStatusPending,
				Metadata: map[string]any{"origin_sender_id": "896694335670726676", "origin_sender_name": "Turti"}}
			if err := ts.CreateTask(ctx, task); err != nil {
				t.Fatalf("CreateTask: %v", err)
			}
			if err := finish(task.ID); err != nil {
				t.Fatalf("finish: %v", err)
			}
			got, err := ts.GetTask(ctx, task.ID)
			if err != nil {
				t.Fatalf("GetTask: %v", err)
			}
			if _, has := got.Metadata["origin_sender_name"]; has {
				t.Errorf("origin_sender_name kept after %s: %v", name, got.Metadata)
			}
			if got.Metadata["origin_sender_id"] != "896694335670726676" {
				t.Errorf("origin_sender_id = %v, want it kept", got.Metadata["origin_sender_id"])
			}
		})
	}
}
