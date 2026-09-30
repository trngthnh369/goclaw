package tools

import (
	"testing"
	"time"
)

// drainOnce enqueues metas on one session and returns the batch meta. The cap
// equals the item count so the queue drains right after the last Enqueue, with
// no dependence on the debounce timer (deterministic under load).
func drainOnce(t *testing.T, metas ...AnnounceMetadata) AnnounceMetadata {
	t.Helper()
	got := make(chan AnnounceMetadata, len(metas))
	q := NewAnnounceQueue(60_000, len(metas), func(_ string, _ []AnnounceQueueItem, meta AnnounceMetadata) {
		got <- meta
	})
	for i, m := range metas {
		q.Enqueue("agent:lead:discord:group:1", AnnounceQueueItem{Label: string(rune('a' + i))}, m)
	}
	select {
	case m := <-got:
		return m
	case <-time.After(5 * time.Second):
		t.Fatal("announce queue did not drain")
		return AnnounceMetadata{}
	}
}

func TestAnnounceQueue_SameOrigin_KeepsPrivilege(t *testing.T) {
	owner := AnnounceMetadata{ParentAgent: "lead", OriginSenderID: "896694335670726676", OriginRole: "owner"}
	renamed := owner
	renamed.OriginSenderID = "896694335670726676|Turti"

	meta := drainOnce(t, owner, renamed)

	if meta.OriginSenderID != "896694335670726676" || meta.OriginRole != "owner" {
		t.Errorf("sender=%q role=%q, want the shared origin kept", meta.OriginSenderID, meta.OriginRole)
	}
}

func TestAnnounceQueue_MixedOrigin_DropsPrivilege(t *testing.T) {
	owner := AnnounceMetadata{ParentAgent: "lead", OriginSenderID: "896694335670726676", OriginSenderName: "Turti", OriginRole: "owner"}
	other := AnnounceMetadata{ParentAgent: "lead", OriginSenderID: "111"}

	cases := map[string][]AnnounceMetadata{
		"owner first":        {owner, other},
		"unprivileged first": {other, owner},
		"owner again after":  {owner, other, owner},
	}
	for name, metas := range cases {
		t.Run(name, func(t *testing.T) {
			meta := drainOnce(t, metas...)
			if meta.OriginSenderID != "" || meta.OriginSenderName != "" || meta.OriginRole != "" {
				t.Errorf("sender=%q name=%q role=%q, want all dropped once the batch mixes users", meta.OriginSenderID, meta.OriginSenderName, meta.OriginRole)
			}
			if meta.ParentAgent != "lead" {
				t.Errorf("ParentAgent = %q, want routing meta kept", meta.ParentAgent)
			}
		})
	}
}
