package cmd

import (
	"context"
	"database/sql"
	"fmt"
	"time"

	"github.com/spf13/cobra"

	"github.com/nextlevelbuilder/goclaw/internal/channels/facebook"
	"github.com/nextlevelbuilder/goclaw/internal/config"
)

func init() {
	var channelName string

	cmd := &cobra.Command{
		Use:   "fb-reels-probe",
		Short: "Check that the page token can publish Reels, without publishing anything",
		Long: `Open a Reels upload session for the page and read its status, then stop.

Nothing is uploaded or published; the session expires unused. Run this after
granting the page token its permissions and before switching Reels publishing
on, so a missing permission shows up here instead of on the first video a
person approved - an approval whose publish fails after upload is reserved
and cannot simply be retried.`,
		RunE: func(cmd *cobra.Command, _ []string) error {
			ctx, cancel := context.WithTimeout(cmd.Context(), 60*time.Second)
			defer cancel()
			token, err := loadPageToken(ctx, channelName)
			if err != nil {
				return err
			}
			pageID, err := loadPageID(ctx, channelName)
			if err != nil {
				return err
			}
			videoID, status, err := facebook.ProbeReelsPublishing(ctx, token, pageID)
			if err != nil {
				return fmt.Errorf("reels probe for page %s failed: %w", pageID, err)
			}
			fmt.Printf("OK page %s can open a Reels upload session (video_id %s, status %q). Nothing was published.\n",
				pageID, videoID, status)
			return nil
		},
	}
	cmd.Flags().StringVar(&channelName, "channel", "fb-page", "facebook channel instance holding the page token")
	rootCmd.AddCommand(cmd)
}

func loadPageID(ctx context.Context, channelName string) (string, error) {
	cfg, err := config.Load(resolveConfigPath())
	if err != nil {
		return "", fmt.Errorf("load config: %w", err)
	}
	db, err := sql.Open("pgx", cfg.Database.PostgresDSN)
	if err != nil {
		return "", fmt.Errorf("open db: %w", err)
	}
	defer db.Close()
	var pageID sql.NullString
	if err := db.QueryRowContext(ctx,
		`SELECT config->>'page_id' FROM channel_instances WHERE name = $1`, channelName).Scan(&pageID); err != nil {
		return "", fmt.Errorf("read channel %q: %w", channelName, err)
	}
	if !pageID.Valid || pageID.String == "" {
		return "", fmt.Errorf("channel %q has no page_id in its config", channelName)
	}
	return pageID.String, nil
}
