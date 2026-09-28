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
