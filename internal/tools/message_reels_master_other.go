//go:build !linux

package tools

import (
	"context"
	"errors"
)

// Master-mode Reels drafts need Linux (O_TMPFILE, execute-only media tools);
// elsewhere, for example the desktop build, they are refused.

func (t *MessageTool) sendReelsMasterDraft(context.Context, string, string, string, string) *Result {
	return ErrorResult("reels_master is only supported on the Linux gateway")
}

func (t *MessageTool) loadReelsDraft(context.Context, string) (reelsDraftRecord, string, error) {
	return reelsDraftRecord{}, "", errors.New("master-mode drafts need the Linux gateway")
}

func (t *MessageTool) removeReelsDraft(context.Context, string, string) {}
