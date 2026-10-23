package tools

import (
	"context"
	"testing"

	"github.com/google/uuid"

	"github.com/nextlevelbuilder/goclaw/internal/store"
)

// UpdateTask rejects "status", so an undispatchable task used to stay in its
// old status forever; failUndispatchableTask must actually end it.
func TestFailUndispatchableTask_EndsOpenTasks(t *testing.T) {
	cases := []struct {
		from, want string
	}{
		{store.TeamTaskStatusPending, store.TeamTaskStatusFailed},
		{store.TeamTaskStatusBlocked, store.TeamTaskStatusFailed},
		{store.TeamTaskStatusInProgress, store.TeamTaskStatusFailed},
		{store.TeamTaskStatusCompleted, store.TeamTaskStatusCompleted},
	}
	for _, tc := range cases {
		t.Run(tc.from, func(t *testing.T) {
			ts := newMockTaskStore(&store.TeamData{}, nil)
			id := uuid.New()
			ts.tasks[id] = &store.TeamTaskData{Status: tc.from}

			failUndispatchableTask(context.Background(), ts, id, uuid.New(), "lead cannot run its own task")

			if got := ts.tasks[id].Status; got != tc.want {
				t.Errorf("status = %q, want %q", got, tc.want)
			}
		})
	}
}
