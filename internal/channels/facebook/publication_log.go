package facebook

import (
	"encoding/json"
	"fmt"
	"log/slog"
	"os"
	"path/filepath"
	"time"
	"unicode/utf8"

	"github.com/nextlevelbuilder/goclaw/internal/bus"
	"github.com/nextlevelbuilder/goclaw/internal/config"
)

// publicationLogDir holds one record per published feed post, under the same
// data dir as the feed-post ledger but deliberately separate from it: the
// ledger is the write-ahead reservation that makes publishing safe, and nothing
// about measurement should be able to disturb it.
const publicationLogDir = ".goclaw/publications"

// PublicationRecord is what was published, and how it performed.
//
// It exists because the pipeline had no path from an outcome back to the work
// that produced it: seven posts reached zero people while the research stage
// kept spending ~1.6M tokens a run, and nothing in the system could notice.
// post_id is the missing link — it is created inside the channel and never
// reaches the tool layer, so it was previously only ever written to a log line.
type PublicationRecord struct {
	Version     int                 `json:"version"`
	PostID      string              `json:"post_id"`
	PageID      string              `json:"page_id"`
	Permalink   string              `json:"permalink,omitempty"`
	PublishedAt time.Time           `json:"published_at"`
	Chars       int                 `json:"chars"`
	Bytes       int                 `json:"bytes"`
	Opening     string              `json:"opening"`
	HasImage    bool                `json:"has_image"`
	Samples     []PublicationSample `json:"samples,omitempty"`
}

// PublicationSample is one engagement reading. Kept as a series rather than
// overwritten: a post's first day and its third say different things, and
// collapsing them would hide which one moved.
type PublicationSample struct {
	At        time.Time `json:"at"`
	AgeHours  float64   `json:"age_hours"`
	Reactions int       `json:"reactions"`
	Comments  int       `json:"comments"`
	Shares    int       `json:"shares"`
}

// PublicationDir returns the directory holding publication records.
func PublicationDir() string {
	return filepath.Join(config.ResolvedDataDirFromEnv(), publicationLogDir)
}

func publicationPath(postID string) string {
	// Post ids are "{page}_{post}"; both halves are digits, so this is a safe
	// file name without further escaping.
	return filepath.Join(PublicationDir(), postID+".json")
}

// LoadPublications reads every record, newest first. A single unreadable file
// is skipped rather than failing the batch — a corrupt record must not hide the
// rest of the history.
func LoadPublications() ([]PublicationRecord, error) {
	entries, err := os.ReadDir(PublicationDir())
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}
	var out []PublicationRecord
	for _, e := range entries {
		if e.IsDir() || filepath.Ext(e.Name()) != ".json" {
			continue
		}
		raw, err := os.ReadFile(filepath.Join(PublicationDir(), e.Name()))
		if err != nil {
			slog.Warn("publications: unreadable record", "file", e.Name(), "error", err)
			continue
		}
		var rec PublicationRecord
		if err := json.Unmarshal(raw, &rec); err != nil {
			slog.Warn("publications: malformed record", "file", e.Name(), "error", err)
			continue
		}
		out = append(out, rec)
	}
	for i := 0; i < len(out); i++ {
		for j := i + 1; j < len(out); j++ {
			if out[j].PublishedAt.After(out[i].PublishedAt) {
				out[i], out[j] = out[j], out[i]
			}
		}
	}
	return out, nil
}

// SavePublication writes a record, replacing any earlier version of it.
func SavePublication(rec PublicationRecord) error {
	if rec.PostID == "" {
		return fmt.Errorf("publications: post_id is required")
	}
	if err := os.MkdirAll(PublicationDir(), 0o755); err != nil {
		return err
	}
	raw, err := json.Marshal(rec)
	if err != nil {
		return err
	}
	return os.WriteFile(publicationPath(rec.PostID), raw, 0o644)
}

// recordPublication persists what was just published. Best-effort: a failure
// here must never turn a successful post into a reported failure.
func (ch *Channel) recordPublication(msg bus.OutboundMessage, postID, permalink string) {
	opening := msg.Content
	if r := []rune(opening); len(r) > 120 {
		opening = string(r[:120]) + "..."
	}
	rec := PublicationRecord{
		Version:     1,
		PostID:      postID,
		PageID:      ch.graphClient.pageID,
		Permalink:   permalink,
		PublishedAt: time.Now().UTC(),
		Chars:       utf8.RuneCountInString(msg.Content),
		Bytes:       len(msg.Content),
		Opening:     opening,
		HasImage:    len(msg.Media) > 0,
	}
	if err := SavePublication(rec); err != nil {
		slog.Warn("publications: record failed", "post_id", postID, "error", err)
		return
	}
	slog.Info("publications: recorded", "post_id", postID, "chars", rec.Chars, "bytes", rec.Bytes)
}
