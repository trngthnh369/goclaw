package cmd

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"text/tabwriter"
	"time"

	"github.com/spf13/cobra"

	"github.com/nextlevelbuilder/goclaw/internal/channels/facebook"
	"github.com/nextlevelbuilder/goclaw/internal/config"
	"github.com/nextlevelbuilder/goclaw/internal/crypto"
)

const graphAPIBase = "https://graph.facebook.com/v21.0"

func init() {
	var (
		channelName string
		days        int
		noFetch     bool
	)

	cmd := &cobra.Command{
		Use:   "fb-metrics",
		Short: "Show engagement for published fanpage posts",
		Long: `Read engagement for posts this gateway published, and record it.

Closes the loop the content pipeline was missing: it publishes, and nothing
measured whether anyone read the result. Each run appends a sample to the
post's publication record, so a post's first day and its third stay
distinguishable.

Posts are discovered from the publication records written at publish time —
post_id is created inside the channel and never reaches the tool layer, so
this is the only durable copy.`,
		RunE: func(cmd *cobra.Command, _ []string) error {
			records, err := facebook.LoadPublications()
			if err != nil {
				if os.IsPermission(err) {
					// The data dir is 0700 owned by the gateway user, and
					// `docker exec` lands as root without CAP_DAC_OVERRIDE, so
					// root is refused here. Say so instead of leaving a bare
					// "permission denied".
					return fmt.Errorf("read publication records: %w\n\n"+
						"The data dir belongs to the gateway user. Run as that user, e.g.\n"+
						"  docker exec -u goclaw <container> /app/goclaw fb-metrics", err)
				}
				return fmt.Errorf("read publication records: %w", err)
			}
			if len(records) == 0 {
				fmt.Printf("No publication records under %s.\n", facebook.PublicationDir())
				fmt.Println("Records are written when a post is published; nothing published since this was added.")
				return nil
			}

			cutoff := time.Now().AddDate(0, 0, -days)
			var token string
			if !noFetch {
				token, err = loadPageToken(cmd.Context(), channelName)
				if err != nil {
					return err
				}
			}

			w := tabwriter.NewWriter(os.Stdout, 0, 0, 2, ' ', 0)
			fmt.Fprintln(w, "PUBLISHED\tCHARS\tBYTES\tIMG\tREACT\tCOMM\tSHARE\tOPENING")
			var fetched, skipped int
			for _, rec := range records {
				if rec.PublishedAt.Before(cutoff) {
					skipped++
					continue
				}
				react, comm, share := lastSample(rec)
				if !noFetch {
					r, c, s, err := fetchEngagement(cmd.Context(), token, rec.PostID)
					if err != nil {
						// A deleted post 404s here; that is normal and must not
						// stop the rest of the report.
						fmt.Fprintf(os.Stderr, "  ! %s: %v\n", rec.PostID, err)
					} else {
						react, comm, share = r, c, s
						rec.Samples = append(rec.Samples, facebook.PublicationSample{
							At:        time.Now().UTC(),
							AgeHours:  time.Since(rec.PublishedAt).Hours(),
							Reactions: r, Comments: c, Shares: s,
						})
						if err := facebook.SavePublication(rec); err != nil {
							fmt.Fprintf(os.Stderr, "  ! save %s: %v\n", rec.PostID, err)
						}
						fetched++
					}
				}
				img := "-"
				if rec.HasImage {
					img = "yes"
				}
				fmt.Fprintf(w, "%s\t%d\t%d\t%s\t%d\t%d\t%d\t%s\n",
					rec.PublishedAt.Local().Format("2006-01-02 15:04"),
					rec.Chars, rec.Bytes, img, react, comm, share,
					strings.ReplaceAll(truncateRunes(rec.Opening, 48), "\n", " "))
			}
			_ = w.Flush()

			fmt.Printf("\n%d post(s) in the last %dd", len(records)-skipped, days)
			if skipped > 0 {
				fmt.Printf(", %d older skipped", skipped)
			}
			if !noFetch {
				fmt.Printf(", %d refreshed", fetched)
			}
			fmt.Println(".")
			return nil
		},
	}

	cmd.Flags().StringVar(&channelName, "channel", "fb-page", "channel instance holding the page token")
	cmd.Flags().IntVar(&days, "days", 30, "only report posts published within this many days")
	cmd.Flags().BoolVar(&noFetch, "no-fetch", false, "show stored samples without calling the Graph API")
	rootCmd.AddCommand(cmd)
}

func truncateRunes(s string, n int) string {
	r := []rune(s)
	if len(r) <= n {
		return s
	}
	return string(r[:n]) + "..."
}

// lastSample returns the most recent stored reading, or zeros.
func lastSample(rec facebook.PublicationRecord) (react, comm, share int) {
	if len(rec.Samples) == 0 {
		return 0, 0, 0
	}
	s := rec.Samples[len(rec.Samples)-1]
	return s.Reactions, s.Comments, s.Shares
}

// loadPageToken decrypts the page access token from the channel instance row.
// The value is never printed.
func loadPageToken(ctx context.Context, channelName string) (string, error) {
	cfg, err := config.Load(resolveConfigPath())
	if err != nil {
		return "", fmt.Errorf("load config: %w", err)
	}
	db, err := sql.Open("pgx", cfg.Database.PostgresDSN)
	if err != nil {
		return "", fmt.Errorf("open db: %w", err)
	}
	defer db.Close()

	var raw []byte
	if err := db.QueryRowContext(ctx,
		`SELECT credentials FROM channel_instances WHERE name = $1`, channelName).Scan(&raw); err != nil {
		return "", fmt.Errorf("read channel %q: %w", channelName, err)
	}
	encKey := os.Getenv("GOCLAW_ENCRYPTION_KEY")
	if encKey == "" {
		return "", fmt.Errorf("GOCLAW_ENCRYPTION_KEY is not set")
	}
	plain, err := crypto.Decrypt(string(raw), encKey)
	if err != nil {
		return "", fmt.Errorf("decrypt credentials: %w", err)
	}
	var creds struct {
		PageAccessToken string `json:"page_access_token"`
	}
	if err := json.Unmarshal([]byte(plain), &creds); err != nil {
		return "", fmt.Errorf("parse credentials: %w", err)
	}
	if creds.PageAccessToken == "" {
		return "", fmt.Errorf("channel %q has no page_access_token", channelName)
	}
	return creds.PageAccessToken, nil
}

func fetchEngagement(ctx context.Context, token, postID string) (react, comm, share int, err error) {
	url := fmt.Sprintf("%s/%s?fields=reactions.summary(true).limit(0),comments.summary(true).limit(0),shares&access_token=%s",
		graphAPIBase, postID, token)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return 0, 0, 0, err
	}
	resp, err := (&http.Client{Timeout: 30 * time.Second}).Do(req)
	if err != nil {
		return 0, 0, 0, err
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != http.StatusOK {
		// Graph's own text; it never echoes the token, which is sent as a query
		// parameter but not reflected in error bodies.
		return 0, 0, 0, fmt.Errorf("graph HTTP %d: %s", resp.StatusCode, truncateRunes(string(body), 160))
	}
	var out struct {
		Reactions struct {
			Summary struct {
				TotalCount int `json:"total_count"`
			} `json:"summary"`
		} `json:"reactions"`
		Comments struct {
			Summary struct {
				TotalCount int `json:"total_count"`
			} `json:"summary"`
		} `json:"comments"`
		Shares struct {
			Count int `json:"count"`
		} `json:"shares"`
	}
	if err := json.Unmarshal(body, &out); err != nil {
		return 0, 0, 0, err
	}
	return out.Reactions.Summary.TotalCount, out.Comments.Summary.TotalCount, out.Shares.Count, nil
}
