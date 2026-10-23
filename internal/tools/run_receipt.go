package tools

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"sync"
	"time"

	"github.com/nextlevelbuilder/goclaw/internal/store"
)

// RunReceiptEnv names the variable that carries a run's receipt token into the
// processes `exec` starts.
const RunReceiptEnv = "GOCLAW_RUN_RECEIPT"

// runReceiptTTL bounds a token whose run never reports its end.
const runReceiptTTL = 30 * time.Minute

// RunReceipt is what the gateway knows about the turn that started a run.
//
// A skill that must act only on a person's word (for example passing an
// escalated review) cannot trust anything the model can write: a file, an env
// var in the command, or its own quote of the person. It asks the gateway
// instead, with the token of the run it executes in. A cron run has no human
// turn, so its receipt says so even if the model runs the command.
type RunReceipt struct {
	RunID            string `json:"run_id"`
	AgentKey         string `json:"agent_key"`
	Channel          string `json:"channel"`
	ChannelType      string `json:"channel_type"`
	HumanReply       bool   `json:"human_reply"`
	SenderID         string `json:"sender_id,omitempty"`
	ReplyToMessageID string `json:"reply_to_message_id,omitempty"`
	ReplyToContent   string `json:"reply_to_content,omitempty"`
	CurrentMessage   string `json:"current_message,omitempty"`
	expires          time.Time
}

type runReceiptStore struct {
	mu      sync.Mutex
	byToken map[string]RunReceipt
	byRun   map[string]string
}

var runReceipts = &runReceiptStore{byToken: map[string]RunReceipt{}, byRun: map[string]string{}}

// RunReceiptToken returns the token for the run in ctx, minting it on first use.
// It returns "" when ctx carries no run.
func RunReceiptToken(ctx context.Context) string {
	rc := store.RunContextFromCtx(ctx)
	if rc == nil || rc.RunID == "" {
		return ""
	}
	return runReceipts.token(rc, time.Now())
}

func (s *runReceiptStore) token(rc *store.RunContext, now time.Time) string {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.pruneLocked(now)
	if tok, ok := s.byRun[rc.RunID]; ok {
		return tok
	}
	buf := make([]byte, 16)
	if _, err := rand.Read(buf); err != nil {
		return ""
	}
	tok := hex.EncodeToString(buf)
	receipt := RunReceipt{RunID: rc.RunID, AgentKey: rc.AgentKey, Channel: rc.Channel, ChannelType: rc.ChannelType,
		HumanReply: rc.IsAllowlistedReplyToBot(), expires: now.Add(runReceiptTTL)}
	if receipt.HumanReply {
		receipt.SenderID = rc.SenderID
		receipt.ReplyToMessageID = rc.ReplyToMessageID
		receipt.ReplyToContent = rc.ReplyToContent
		receipt.CurrentMessage = rc.CurrentMessage
	}
	s.byToken[tok] = receipt
	s.byRun[rc.RunID] = tok
	return tok
}

func (s *runReceiptStore) pruneLocked(now time.Time) {
	for tok, r := range s.byToken {
		if now.After(r.expires) {
			delete(s.byToken, tok)
			delete(s.byRun, r.RunID)
		}
	}
}

// LookupRunReceipt returns the receipt for a live token.
func LookupRunReceipt(token string) (RunReceipt, bool) {
	runReceipts.mu.Lock()
	defer runReceipts.mu.Unlock()
	r, ok := runReceipts.byToken[token]
	if !ok || time.Now().After(r.expires) {
		return RunReceipt{}, false
	}
	return r, true
}

// ReleaseRunReceipt ends a run's token, so one saved to a file during a run
// with a human turn is dead before the next run.
func ReleaseRunReceipt(runID string) {
	runReceipts.mu.Lock()
	defer runReceipts.mu.Unlock()
	if tok, ok := runReceipts.byRun[runID]; ok {
		delete(runReceipts.byToken, tok)
		delete(runReceipts.byRun, runID)
	}
}
