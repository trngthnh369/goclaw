package cmd

import (
	"context"
	"fmt"
	"log/slog"
	"path/filepath"
	"strings"

	"github.com/google/uuid"

	"github.com/nextlevelbuilder/goclaw/internal/agent"
	"github.com/nextlevelbuilder/goclaw/internal/bus"
	"github.com/nextlevelbuilder/goclaw/internal/config"
	orch "github.com/nextlevelbuilder/goclaw/internal/orchestration"
	"github.com/nextlevelbuilder/goclaw/internal/scheduler"
	"github.com/nextlevelbuilder/goclaw/internal/sessions"
	"github.com/nextlevelbuilder/goclaw/internal/store"
	"github.com/nextlevelbuilder/goclaw/internal/tools"
)

// announceEntry holds one teammate completion result waiting to be announced.
type announceEntry struct {
	MemberAgent       string // agent key (e.g. "researcher")
	MemberDisplayName string // display name (e.g. "Nhà Nghiên Cứu"), empty if not set
	Content           string
	Media             []agent.MediaResult
	OriginUserID      string // user scope of the turn that created this entry's task
	OriginSenderID    string // real user who created this entry's task (#915)
	OriginRole        string // that user's RBAC role at task creation
}

// teamAnnounceQueue uses BatchQueue for producer-consumer synchronization.
var teamAnnounceQueue orch.BatchQueue[announceEntry]

// enqueueAnnounce adds a result to the queue. Returns isProcessor.
// If isProcessor=true, the caller must run processAnnounceLoop.
func enqueueAnnounce(key string, entry announceEntry) bool {
	return teamAnnounceQueue.Enqueue(key, entry)
}

// announceRouting holds the shared routing info captured by the first goroutine.
type announceRouting struct {
	LeadAgent        string
	LeadSessionKey   string
	OrigChannel      string
	OrigChatID       string
	OrigPeerKind     string
	OrigLocalKey     string
	OrigChannelType  string // platform type (e.g. "discord"); the prompt must match the user's turn
	OriginUserID     string
	TeamID           string
	TeamWorkspace    string
	OriginTraceID    string
	ParentTraceID    uuid.UUID
	ParentRootSpanID uuid.UUID
	OutMeta          map[string]string
}

// processAnnounceLoop drains entries, builds merged announce, schedules to leader.
// Loops until queue is empty.
func processAnnounceLoop(
	ctx context.Context,
	r announceRouting,
	sched *scheduler.Scheduler,
	msgBus *bus.MessageBus,
	teamStore store.TeamStore,
	postTurn tools.PostTurnProcessor,
	cfg *config.Config,
) {
	for {
		entries := teamAnnounceQueue.Drain(r.LeadSessionKey)
		if len(entries) == 0 {
			if teamAnnounceQueue.TryFinish(r.LeadSessionKey) {
				return
			}
			continue // entries arrived between drain and tryFinish
		}

		// Build task board snapshot (latest state, after all completions in this batch).
		snapshot := ""
		if r.TeamID != "" && r.OriginTraceID != "" {
			if teamUUID, err := uuid.Parse(r.TeamID); err == nil {
				snapshot = buildTaskBoardSnapshot(ctx, teamStore, teamUUID, r.OrigChatID, r.OriginTraceID)
			}
		}

		content := buildMergedAnnounceContent(entries, snapshot, r.TeamWorkspace)

		req := buildTeamAnnounceRunRequest(r, entries, content)
		// Collect all media from entries.
		for _, e := range entries {
			for _, mr := range e.Media {
				req.ForwardMedia = append(req.ForwardMedia, bus.MediaFile{
					Path:     mr.Path,
					MimeType: mr.ContentType,
					Filename: filepath.Base(mr.Path), // preserve sanitized stem from producer
				})
			}
		}
		// WS channel has no outbound media handler — deliver via ContentSuffix.
		if r.OrigChannel == "ws" && len(req.ForwardMedia) > 0 {
			req.ContentSuffix = mediaToMarkdownFromPaths(req.ForwardMedia, cfg)
			req.ForwardMedia = nil
		}

		// Process batch in closure so defer is scoped per iteration (panic safety).
		func() {
			ptd := tools.NewPendingTeamDispatch()
			defer ptd.ReleaseTeamLock()
			schedCtx := tools.WithPendingTeamDispatch(ctx, ptd)
			outCh := sched.Schedule(schedCtx, scheduler.LaneSubagent, req)
			outcome := <-outCh

			ptd.ReleaseTeamLock()
			if postTurn != nil {
				for tid, tIDs := range ptd.Drain() {
					if err := postTurn.ProcessPendingTasks(ctx, tid, tIDs); err != nil {
						slog.Warn("post_turn(announce): failed", "team_id", tid, "error", err)
					}
				}
			}

			if outcome.Err != nil {
				slog.Error("teammate announce: lead run failed", "error", outcome.Err, "batch_size", len(entries))
			} else {
				isSilent := outcome.Result.Content == "" || agent.IsSilentReply(outcome.Result.Content)
				if !(isSilent && len(outcome.Result.Media) == 0) {
					out := outcome.Result.Content
					if isSilent {
						out = ""
					}
					outMsg := bus.OutboundMessage{
						Channel:  r.OrigChannel,
						ChatID:   r.OrigChatID,
						Content:  out,
						Metadata: r.OutMeta,
					}
					appendMediaToOutbound(&outMsg, outcome.Result.Media)
					msgBus.PublishOutbound(outMsg)
				}
			}

			slog.Info("teammate announce: batch processed",
				"batch_size", len(entries), "session", r.LeadSessionKey)
		}()

		// Loop back — tryFinish at top will exit when queue is truly empty.
	}
}

// memberLabel returns a display-friendly name for announce messages.
func memberLabel(e announceEntry) string {
	if e.MemberDisplayName != "" {
		return fmt.Sprintf("%s (%s)", e.MemberDisplayName, e.MemberAgent)
	}
	return e.MemberAgent
}

// buildMergedAnnounceContent creates the announce message for one or more completed/failed tasks.
func buildMergedAnnounceContent(entries []announceEntry, taskBoardSnapshot, teamWorkspace string) string {
	var sb strings.Builder

	if len(entries) == 1 {
		e := entries[0]
		label := memberLabel(e)
		if strings.HasPrefix(e.Content, "[FAILED]") {
			fmt.Fprintf(&sb, "[System Message] Team member %q failed to complete task.\n\nError: %s", label, strings.TrimPrefix(e.Content, "[FAILED] "))
			sb.WriteString("\n\nInform the user about the failure. You may suggest retrying with team_tasks(action=\"retry\", task_id=\"...\") if appropriate.")
		} else {
			fmt.Fprintf(&sb, "[System Message] Team member %q completed task.\n\nResult:\n%s", label, e.Content)
		}
	} else {
		// Count successes vs failures for header.
		var failed, succeeded int
		for _, e := range entries {
			if strings.HasPrefix(e.Content, "[FAILED]") {
				failed++
			} else {
				succeeded++
			}
		}
		if failed > 0 && succeeded > 0 {
			fmt.Fprintf(&sb, "[System Message] %d task(s) completed, %d task(s) failed.\n", succeeded, failed)
		} else if failed > 0 {
			fmt.Fprintf(&sb, "[System Message] %d task(s) failed.\n", failed)
		} else {
			fmt.Fprintf(&sb, "[System Message] %d team tasks completed.\n", succeeded)
		}
		for _, e := range entries {
			label := memberLabel(e)
			if strings.HasPrefix(e.Content, "[FAILED]") {
				fmt.Fprintf(&sb, "\n--- FAILED: %q ---\nError: %s\n", label, strings.TrimPrefix(e.Content, "[FAILED] "))
			} else {
				fmt.Fprintf(&sb, "\n--- Result from %q ---\n%s\n", label, e.Content)
			}
		}
		if failed > 0 {
			sb.WriteString("\nFor failed tasks, you may suggest retrying with team_tasks(action=\"retry\", task_id=\"...\").")
		}
	}

	if taskBoardSnapshot != "" {
		sb.WriteString("\n\n")
		sb.WriteString(taskBoardSnapshot)
	}

	// Batch-aware prompting: guide leader to summarize vs acknowledge.
	allDone := strings.Contains(taskBoardSnapshot, "All ") && strings.Contains(taskBoardSnapshot, " completed")
	if allDone {
		sb.WriteString("\n\nAll tasks in this batch are completed. Present a comprehensive summary of ALL results to the user.")
	} else if taskBoardSnapshot != "" {
		sb.WriteString("\n\nSome tasks are still in progress. Briefly acknowledge this result (1-2 sentences). A full summary will come when all tasks complete.")
	} else {
		sb.WriteString("\n\nPresent this result to the user.")
	}

	sb.WriteString(" Any media files are forwarded automatically. Do NOT search for files — the results above contain all relevant information.")

	if teamWorkspace != "" {
		fmt.Fprintf(&sb, "\n[Team workspace: %s — use read_file/list_files to access shared files]", teamWorkspace)
	}

	return sb.String()
}

// buildTeamAnnounceRunRequest builds the lead's run for a batch of member
// results. It carries the channel type, user scope and group guidance of the
// user's turn that created the tasks, so the lead runs in the same context
// (workspace, USER.md, memory) and the stable part of its system prompt matches
// that turn, which keeps the provider prompt cache warm. Sender and role come
// from announceBatchPrivilege. Per-topic prompts, channel self-identity and
// Bitrix24 hints are not carried: those only land in the dynamic prompt part.
func buildTeamAnnounceRunRequest(r announceRouting, entries []announceEntry, content string) agent.RunRequest {
	senderID, role := announceBatchPrivilege(r, entries)
	req := agent.RunRequest{
		Surface:          tools.SurfaceSubagent,
		SessionKey:       r.LeadSessionKey,
		Message:          content,
		Channel:          r.OrigChannel,
		ChannelType:      r.OrigChannelType,
		ChatID:           r.OrigChatID,
		PeerKind:         r.OrigPeerKind,
		LocalKey:         r.OrigLocalKey,
		UserID:           announceBatchUserID(r, entries),
		SenderID:         senderID,
		Role:             role,
		RunID:            fmt.Sprintf("teammate-announce-%s-%d", r.LeadAgent, len(entries)),
		RunKind:          "announce",
		HideInput:        true,
		Stream:           false,
		TeamID:           r.TeamID,
		ParentTraceID:    r.ParentTraceID,
		ParentRootSpanID: r.ParentRootSpanID,
	}
	if r.OrigPeerKind == string(sessions.PeerGroup) {
		req.ExtraSystemPrompt = groupChatExtraPrompt()
	}
	return req
}

// announceSenderID keeps only a real user as the announce turn's sender; an
// internal sender (teammate:, system:, ...) must not be attributed writes.
func announceSenderID(sender string) string {
	if sender == "" || bus.IsInternalSender(sender) {
		return ""
	}
	return sender
}

// announceBatchPrivilege returns the sender and role the lead's announce turn
// acts with. A batch merges every result that completes while the lead is busy,
// keyed by lead+team+chat, so it can hold tasks created by different users in
// one group. Their write permissions must not mix: only a batch whose entries
// all share one sender and role inherits them; otherwise the turn runs with no
// sender, which denies group writes as before (#915).
func announceBatchPrivilege(r announceRouting, entries []announceEntry) (senderID, role string) {
	senderID, role, mixed := commonOriginPrivilege(len(entries), func(i int) (string, string) {
		return entries[i].OriginSenderID, entries[i].OriginRole
	})
	if mixed {
		slog.Warn("security.team_announce.mixed_origin",
			"session", r.LeadSessionKey, "team_id", r.TeamID, "batch_size", len(entries))
	}
	return senderID, role
}

// commonOriginPrivilege returns the sender and role that all n batch items
// share. When any item differs it returns two empty strings and mixed=true:
// a lead turn over results from different users must not act with any one of
// their permissions (#915).
func commonOriginPrivilege(n int, at func(i int) (sender, role string)) (sender, role string, mixed bool) {
	if n == 0 {
		return "", "", false
	}
	sender, role = at(0)
	for i := 1; i < n; i++ {
		if s, r := at(i); s != sender || r != role {
			return "", "", true
		}
	}
	return sender, role, false
}

// announceBatchUserID picks the user scope (workspace, USER.md, memory) for the
// lead's announce turn. A batch whose tasks all came from one user runs in that
// user's scope. A batch mixing users in a group runs in the shared group scope,
// so one user's private context is not loaded into a reply that summarizes
// another user's results in the group.
func announceBatchUserID(r announceRouting, entries []announceEntry) string {
	uid := r.OriginUserID
	for i, e := range entries {
		if i == 0 {
			if e.OriginUserID != "" {
				uid = e.OriginUserID
			}
			continue
		}
		if e.OriginUserID != entries[0].OriginUserID {
			if r.OrigPeerKind == string(sessions.PeerGroup) && r.OrigChatID != "" {
				return fmt.Sprintf("group:%s:%s", r.OrigChannel, r.OrigChatID)
			}
			return r.OriginUserID
		}
	}
	return uid
}
