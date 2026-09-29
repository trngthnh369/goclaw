package tools

import (
	"testing"
	"time"
)

func drainOnce(t *testing.T, metas ...AnnounceMetadata) AnnounceMetadata {
	t.Helper()
	got := make(chan AnnounceMetadata, 1)
	q := NewAnnounceQueue(20, 20, func(_ string, _ []AnnounceQueueItem, meta AnnounceMetadata) {
		got <- meta
	})
	for i, m := range metas {
		q.Enqueue("agent:lead:discord:group:1", AnnounceQueueItem{Label: string(rune('a' + i))}, m)
	}
	select {
	case m := <-got:
		return m
	case <-time.After(2 * time.Second):
		t.Fatal("announce queue did not drain")
		return AnnounceMetadata{}
	}
}

func TestAnnounceQueue_SameOrigin_KeepsPrivilege(t *testing.T) {
	owner := AnnounceMetadata{ParentAgent: "lead", OriginSenderID: "896694335670726676", OriginRole: "owner"}

	meta := drainOnce(t, owner, owner)

	if meta.OriginSenderID != "896694335670726676" || meta.OriginRole != "owner" {
		t.Errorf("sender=%q role=%q, want the shared origin kept", meta.OriginSenderID, meta.OriginRole)
	}
}

func TestAnnounceQueue_MixedOrigin_DropsPrivilege(t *testing.T) {
	owner := AnnounceMetadata{ParentAgent: "lead", OriginSenderID: "896694335670726676", OriginRole: "owner"}
	other := AnnounceMetadata{ParentAgent: "lead", OriginSenderID: "111"}

	meta := drainOnce(t, owner, other, owner)

	if meta.OriginSenderID != "" || meta.OriginRole != "" {
		t.Errorf("sender=%q role=%q, want both dropped once the batch mixes users", meta.OriginSenderID, meta.OriginRole)
	}
	if meta.ParentAgent != "lead" {
		t.Errorf("ParentAgent = %q, want routing meta kept", meta.ParentAgent)
	}
}
