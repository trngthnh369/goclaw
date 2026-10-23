package cmd

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/bwmarrin/discordgo"
	"github.com/spf13/cobra"

	"github.com/nextlevelbuilder/goclaw/internal/config"
	"github.com/nextlevelbuilder/goclaw/internal/crypto"
)

// Rollback helper for Video Factory master-mode Reels drafts (plan 260928-q7m2,
// Phase 3). A gateway binary older than master mode ignores the master_sha256
// line and would publish the attached review cut if such a draft were approved,
// the fallback the user refused. Before swapping to such a binary, disarm every
// pending master-mode draft: the bot rewrites its own message so the caption
// block no longer parses (every binary then refuses it) and drops its 📝 mark.

const (
	disarmCaptionOpen  = "[caption]" // internal/tools/message_reels.go reelsCaptionOpen
	disarmCaptionClose = "[/caption]"
	disarmedOpen       = "(caption)" // same length: the edit never grows the message
	disarmedClose      = "(/caption)"
	disarmMasterLine   = "master_sha256:"
	disarmDraftMarker  = "📝"
	disarmPageSize     = 100
)

// disarmReelsDraftContent returns content with its caption block markers
// neutralised, and whether content is an armed master-mode draft at all.
func disarmReelsDraftContent(content string) (string, bool) {
	lines := strings.Split(content, "\n")
	hasMaster, open, closing := false, -1, -1
	for i, line := range lines {
		switch trimmed := strings.TrimSpace(line); {
		case strings.HasPrefix(trimmed, disarmMasterLine):
			hasMaster = true
		case trimmed == disarmCaptionOpen:
			open = i
		case trimmed == disarmCaptionClose:
			closing = i
		}
	}
	if !hasMaster || (open < 0 && closing < 0) {
		return content, false
	}
	if open >= 0 {
		lines[open] = strings.Replace(lines[open], disarmCaptionOpen, disarmedOpen, 1)
	}
	if closing >= 0 {
		lines[closing] = strings.Replace(lines[closing], disarmCaptionClose, disarmedClose, 1)
	}
	return strings.Join(lines, "\n"), true
}

func init() {
	var channelName string
	var days int
	var apply, selfTest bool

	cmd := &cobra.Command{
		Use:   "reels-disarm",
		Short: "Disarm pending Video Factory master-mode Reels drafts before a binary rollback",
		Long: `List (default) or disarm (--apply) the bot's own master-mode Reels review
drafts in every Reels review chat of a Discord channel instance.

Disarming rewrites [caption]/[/caption] to (caption)/(/caption), so no gateway
binary can parse the caption and publish, and removes the bot's 📝 draft mark.
Run it with the master-mode binary still live, after publish.facebook_reels.master
is off and no draft is being sent; then swap the binary and re-package the jobs.

--self-test posts a fake text-only draft in the first review chat, disarms it,
checks the result and deletes it.`,
		RunE: func(cmd *cobra.Command, _ []string) error {
			ctx, cancel := context.WithTimeout(cmd.Context(), 10*time.Minute)
			defer cancel()
			token, chats, err := loadDiscordReviewChannel(ctx, channelName)
			if err != nil {
				return err
			}
			s, err := discordgo.New("Bot " + token)
			if err != nil {
				return fmt.Errorf("discord session: %w", err)
			}
			me, err := s.User("@me", discordgo.WithContext(ctx))
			if err != nil {
				return fmt.Errorf("discord bot user: %w", err)
			}
			if selfTest {
				return reelsDisarmSelfTest(ctx, s, me.ID, chats[0])
			}
			since := time.Now().Add(-time.Duration(days) * 24 * time.Hour)
			total := 0
			for _, chat := range chats {
				n, err := disarmChat(ctx, s, me.ID, chat, since, apply)
				total += n
				if err != nil {
					return fmt.Errorf("chat %s: %w", chat, err)
				}
			}
			verb := "would disarm"
			if apply {
				verb = "disarmed"
			}
			fmt.Printf("%s %d master-mode draft(s) in %d review chat(s), last %d days\n", verb, total, len(chats), days)
			return nil
		},
	}
	cmd.Flags().StringVar(&channelName, "channel", "vf-discord", "discord channel instance whose Reels review chats to scan")
	cmd.Flags().IntVar(&days, "days", 30, "how far back to scan")
	cmd.Flags().BoolVar(&apply, "apply", false, "edit the drafts (default: list only)")
	cmd.Flags().BoolVar(&selfTest, "self-test", false, "post, disarm, verify and delete one fake draft")
	rootCmd.AddCommand(cmd)
}

func loadDiscordReviewChannel(ctx context.Context, channelName string) (string, []string, error) {
	cfg, err := config.Load(resolveConfigPath())
	if err != nil {
		return "", nil, fmt.Errorf("load config: %w", err)
	}
	db, err := sql.Open("pgx", cfg.Database.PostgresDSN)
	if err != nil {
		return "", nil, fmt.Errorf("open db: %w", err)
	}
	defer db.Close()
	var raw, instCfg []byte
	if err := db.QueryRowContext(ctx,
		`SELECT credentials, config FROM channel_instances WHERE name = $1 AND channel_type = 'discord'`,
		channelName).Scan(&raw, &instCfg); err != nil {
		return "", nil, fmt.Errorf("read discord channel %q: %w", channelName, err)
	}
	encKey := os.Getenv("GOCLAW_ENCRYPTION_KEY")
	if encKey == "" {
		return "", nil, fmt.Errorf("GOCLAW_ENCRYPTION_KEY is not set")
	}
	plain, err := crypto.Decrypt(string(raw), encKey)
	if err != nil {
		return "", nil, fmt.Errorf("decrypt credentials: %w", err)
	}
	var creds struct {
		Token string `json:"token"`
	}
	if err := json.Unmarshal([]byte(plain), &creds); err != nil || creds.Token == "" {
		return "", nil, fmt.Errorf("channel %q has no bot token", channelName)
	}
	var c struct {
		ReelsReviewChatIDs config.FlexibleStringSlice `json:"reels_review_chat_ids"`
	}
	if err := json.Unmarshal(instCfg, &c); err != nil {
		return "", nil, fmt.Errorf("parse channel config: %w", err)
	}
	var chats []string
	for _, id := range c.ReelsReviewChatIDs {
		if id = strings.TrimSpace(id); id != "" {
			chats = append(chats, id)
		}
	}
	if len(chats) == 0 {
		return "", nil, fmt.Errorf("channel %q has no reels_review_chat_ids", channelName)
	}
	return creds.Token, chats, nil
}

// disarmChat pages back through a chat to since, disarming (or listing) each
// armed master-mode draft the bot wrote.
func disarmChat(ctx context.Context, s *discordgo.Session, botID, chat string, since time.Time, apply bool) (int, error) {
	count, before := 0, ""
	for {
		page, err := s.ChannelMessages(chat, disarmPageSize, before, "", "", discordgo.WithContext(ctx))
		if err != nil {
			return count, err
		}
		for _, m := range page {
			if m.Timestamp.Before(since) {
				return count, nil
			}
			if m.Author == nil || m.Author.ID != botID {
				continue
			}
			disarmed, armed := disarmReelsDraftContent(m.Content)
			if !armed {
				continue
			}
			count++
			fmt.Printf("draft %s in %s (%s)\n", m.ID, chat, m.Timestamp.Format(time.RFC3339))
			if !apply {
				continue
			}
			if err := disarmMessage(ctx, s, chat, m.ID, disarmed); err != nil {
				return count, fmt.Errorf("message %s: %w", m.ID, err)
			}
		}
		if len(page) < disarmPageSize {
			return count, nil
		}
		before = page[len(page)-1].ID
	}
}

func disarmMessage(ctx context.Context, s *discordgo.Session, chat, id, content string) error {
	if _, err := s.ChannelMessageEdit(chat, id, content, discordgo.WithContext(ctx)); err != nil {
		return fmt.Errorf("edit: %w", err)
	}
	// Removing a reaction that is not there is not an error worth failing on.
	_ = s.MessageReactionRemove(chat, id, disarmDraftMarker, "@me", discordgo.WithContext(ctx))
	return nil
}

func reelsDisarmSelfTest(ctx context.Context, s *discordgo.Session, botID, chat string) error {
	content := "reels-disarm self-test (deleted in a moment)\n" + disarmCaptionOpen + "\nnot a real caption\n" +
		disarmCaptionClose + "\n" + disarmMasterLine + " " + strings.Repeat("0", 64)
	msg, err := s.ChannelMessageSend(chat, content, discordgo.WithContext(ctx))
	if err != nil {
		return fmt.Errorf("post test draft: %w", err)
	}
	defer func() { _ = s.ChannelMessageDelete(chat, msg.ID) }()
	if err := s.MessageReactionAdd(chat, msg.ID, disarmDraftMarker, discordgo.WithContext(ctx)); err != nil {
		return fmt.Errorf("mark test draft: %w", err)
	}
	// The scan must find the test draft (list only: a real draft is never touched).
	n, err := disarmChat(ctx, s, botID, chat, time.Now().Add(-time.Hour), false)
	if err != nil {
		return err
	}
	if n < 1 {
		return fmt.Errorf("self-test FAILED: the scan did not find the test draft")
	}
	disarmed, _ := disarmReelsDraftContent(content)
	if err := disarmMessage(ctx, s, chat, msg.ID, disarmed); err != nil {
		return err
	}
	got, err := s.ChannelMessage(chat, msg.ID, discordgo.WithContext(ctx))
	if err != nil {
		return fmt.Errorf("re-read test draft: %w", err)
	}
	if _, armed := disarmReelsDraftContent(got.Content); armed || !strings.Contains(got.Content, disarmedOpen) {
		return fmt.Errorf("self-test FAILED: the draft is still armed")
	}
	for _, r := range got.Reactions {
		if r != nil && r.Me && r.Emoji != nil && r.Emoji.Name == disarmDraftMarker {
			return fmt.Errorf("self-test FAILED: the 📝 mark is still there")
		}
	}
	fmt.Printf("self-test OK: scan found %d draft(s) in the last hour of %s (test draft included); "+
		"the test draft was disarmed, its mark removed, and it is deleted\n", n, chat)
	return nil
}
