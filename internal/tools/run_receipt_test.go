package tools

import (
	"context"
	"testing"

	"github.com/nextlevelbuilder/goclaw/internal/store"
)

func humanReplyRunContext(runID string) *store.RunContext {
	return &store.RunContext{RunID: runID, AgentKey: "vf-director", Channel: "vf-discord",
		SenderID: "896694335670726676", ApprovalSenderAllowed: true,
		ReplyToMessageID: "m1", ReplyToAuthorID: "bot", ChannelBotUserID: "bot",
		ReplyToContent: "⚠️ Video Factory · x\njob: vf-1", CurrentMessage: "cứ làm tiếp"}
}

func TestRunReceiptCarriesTheHumanTurnOnlyForAnAllowlistedReplyToTheBot(t *testing.T) {
	human := store.WithRunContext(context.Background(), humanReplyRunContext("run-human"))
	tok := RunReceiptToken(human)
	defer ReleaseRunReceipt("run-human")
	receipt, ok := LookupRunReceipt(tok)
	if !ok || !receipt.HumanReply || receipt.CurrentMessage != "cứ làm tiếp" || receipt.SenderID == "" {
		t.Fatalf("human reply receipt = %+v, found %v", receipt, ok)
	}
	if again := RunReceiptToken(human); again != tok {
		t.Fatalf("one run must keep one token, got %q then %q", tok, again)
	}

	cron := humanReplyRunContext("run-cron")
	cron.ApprovalSenderAllowed, cron.ReplyToMessageID = false, ""
	cronTok := RunReceiptToken(store.WithRunContext(context.Background(), cron))
	defer ReleaseRunReceipt("run-cron")
	receipt, ok = LookupRunReceipt(cronTok)
	if !ok || receipt.HumanReply || receipt.CurrentMessage != "" {
		t.Fatalf("a run without a human reply must say so and carry no words: %+v", receipt)
	}

	notBot := humanReplyRunContext("run-not-bot")
	notBot.ReplyToAuthorID = "someone-else"
	notBotTok := RunReceiptToken(store.WithRunContext(context.Background(), notBot))
	defer ReleaseRunReceipt("run-not-bot")
	if receipt, _ = LookupRunReceipt(notBotTok); receipt.HumanReply {
		t.Fatal("a reply to someone else's message is not an answer to the bot")
	}
}

func TestReleasedRunReceiptIsGone(t *testing.T) {
	tok := RunReceiptToken(store.WithRunContext(context.Background(), humanReplyRunContext("run-release")))
	ReleaseRunReceipt("run-release")
	if _, ok := LookupRunReceipt(tok); ok {
		t.Fatal("a token must die with its run")
	}
	if RunReceiptToken(context.Background()) != "" {
		t.Fatal("no run, no token")
	}
}
