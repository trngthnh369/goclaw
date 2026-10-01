//go:build sqlite || sqliteonly

package sqlitestore

import (
	"context"
	"testing"

	"github.com/google/uuid"

	"github.com/nextlevelbuilder/goclaw/internal/store"
)

// The origin sender's display name only renders the announce prompt, so it is
// dropped from the task row once the task ends (UpdateTask cannot change the
// status); the sender ID and other keys stay (#915).
func TestTerminalStatus_ClearsOriginSenderName(t *testing.T) {
	db, tenantID, agentID := newAgentUpdateTestFixture(t)
	teamID := uuid.Must(uuid.NewV7())
	mustExec(t, db,
		`INSERT INTO agent_teams (id, name, lead_agent_id, created_by, tenant_id) VALUES (?, 'crew', ?, 'owner', ?)`,
		teamID.String(), agentID.String(), tenantID.String())
	ts := NewSQLiteTeamStore(db)
	ctx := store.WithTenantID(context.Background(), tenantID)

	claim := func(id uuid.UUID) error { return ts.ClaimTask(ctx, id, agentID, teamID) }
	review := func(id uuid.UUID) error {
		if err := claim(id); err != nil {
			return err
		}
		return ts.ReviewTask(ctx, id, teamID)
	}
	end := map[string]func(id uuid.UUID) error{
		"complete": func(id uuid.UUID) error {
			if err := claim(id); err != nil {
				return err
			}
			return ts.CompleteTask(ctx, id, teamID, "done")
		},
		"fail": func(id uuid.UUID) error {
			if err := claim(id); err != nil {
				return err
			}
			return ts.FailTask(ctx, id, teamID, "boom")
		},
		"approve": func(id uuid.UUID) error {
			if err := review(id); err != nil {
				return err
			}
			return ts.ApproveTask(ctx, id, teamID, "ok")
		},
		"reject": func(id uuid.UUID) error {
			if err := review(id); err != nil {
				return err
			}
			return ts.RejectTask(ctx, id, teamID, "no")
		},
		"cancel":       func(id uuid.UUID) error { return ts.CancelTask(ctx, id, teamID, "stop") },
		"fail pending": func(id uuid.UUID) error { return ts.FailPendingTask(ctx, id, teamID, "invalid") },
	}
	for name, finish := range end {
		t.Run(name, func(t *testing.T) {
			task := &store.TeamTaskData{TeamID: teamID, Subject: "named " + name, Status: store.TeamTaskStatusPending,
				Metadata: map[string]any{"origin_sender_id": "896694335670726676", "origin_sender_name": "Turti", "peer_kind": "group"}}
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
			if got.Metadata["origin_sender_id"] != "896694335670726676" || got.Metadata["peer_kind"] != "group" {
				t.Errorf("other metadata lost after %s: %v", name, got.Metadata)
			}
		})
	}
}

// A malformed metadata row must not make the status change fail.
func TestTerminalStatus_MalformedMetadataStillEnds(t *testing.T) {
	db, tenantID, agentID := newAgentUpdateTestFixture(t)
	teamID := uuid.Must(uuid.NewV7())
	mustExec(t, db,
		`INSERT INTO agent_teams (id, name, lead_agent_id, created_by, tenant_id) VALUES (?, 'crew', ?, 'owner', ?)`,
		teamID.String(), agentID.String(), tenantID.String())
	ts := NewSQLiteTeamStore(db)
	ctx := store.WithTenantID(context.Background(), tenantID)

	task := &store.TeamTaskData{TeamID: teamID, Subject: "broken", Status: store.TeamTaskStatusPending,
		Metadata: map[string]any{"origin_sender_name": "Turti"}}
	if err := ts.CreateTask(ctx, task); err != nil {
		t.Fatalf("CreateTask: %v", err)
	}
	mustExec(t, db, `UPDATE team_tasks SET metadata = 'not json' WHERE id = ?`, task.ID.String())

	if err := ts.CancelTask(ctx, task.ID, teamID, "stop"); err != nil {
		t.Fatalf("CancelTask on malformed metadata: %v", err)
	}
	var status string
	if err := db.QueryRow(`SELECT status FROM team_tasks WHERE id = ?`, task.ID.String()).Scan(&status); err != nil {
		t.Fatalf("read status: %v", err)
	}
	if status != store.TeamTaskStatusCancelled {
		t.Errorf("status = %q, want %q", status, store.TeamTaskStatusCancelled)
	}
}
