package tools

import (
	"context"
	"testing"

	"github.com/nextlevelbuilder/goclaw/internal/store"
)

func TestResolveDispatchOriginUserID(t *testing.T) {
	const (
		guildUser = "guild:1487758328577921066:user:896694335670726676"
		chatID    = "1552179009540857876"
	)
	withMeta := &store.TeamTaskData{Metadata: map[string]any{MetaOriginUserID: guildUser}}
	withoutMeta := &store.TeamTaskData{Metadata: map[string]any{}}

	cases := []struct {
		name string
		ctx  context.Context
		task *store.TeamTaskData
		want string
	}{
		{"lead turn context wins", store.WithUserID(context.Background(), "ctx-user"), withMeta, "ctx-user"},
		{"post-turn dispatch uses stored user", context.Background(), withMeta, guildUser},
		{"legacy task falls back to chat id", context.Background(), withoutMeta, chatID},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := resolveDispatchOriginUserID(tc.ctx, tc.task, chatID); got != tc.want {
				t.Errorf("got %q, want %q", got, tc.want)
			}
		})
	}
}

func TestResolveDispatchOriginSender(t *testing.T) {
	stored := &store.TeamTaskData{Metadata: map[string]any{
		"origin_sender_id":   "896694335670726676",
		MetaOriginSenderName: "Turti",
	}}
	legacy := &store.TeamTaskData{Metadata: map[string]any{"origin_sender_id": "896694335670726676"}}
	empty := &store.TeamTaskData{Metadata: map[string]any{}}
	userCtx := store.WithSenderName(store.WithSenderID(context.Background(), "222"), "Ana")
	tickerCtx := store.WithSenderID(context.Background(), "ticker:system")

	cases := []struct {
		name             string
		ctx              context.Context
		task             *store.TeamTaskData
		wantID, wantName string
	}{
		{"lead turn context wins with its own name", userCtx, stored, "222", "Ana"},
		{"deferred dispatch uses stored sender and name", tickerCtx, stored, "896694335670726676", "Turti"},
		{"no ctx sender uses stored sender and name", context.Background(), stored, "896694335670726676", "Turti"},
		{"task stored before names has no name", context.Background(), legacy, "896694335670726676", ""},
		{"internal sender without fallback passes through unnamed", tickerCtx, empty, "ticker:system", ""},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			id, name := resolveDispatchOriginSender(tc.ctx, tc.task)
			if id != tc.wantID || name != tc.wantName {
				t.Errorf("got (%q, %q), want (%q, %q)", id, name, tc.wantID, tc.wantName)
			}
		})
	}
}
