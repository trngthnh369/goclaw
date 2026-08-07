package facebook

import (
	"strings"
	"testing"
	"time"
)

func TestPublicationRecord_RoundTripAndOrdering(t *testing.T) {
	t.Setenv("GOCLAW_DATA_DIR", t.TempDir())

	older := PublicationRecord{
		Version: 1, PostID: "1_100", PageID: "1",
		PublishedAt: time.Now().UTC().Add(-48 * time.Hour),
		Chars:       1200, Bytes: 1600, Opening: "bài cũ",
	}
	newer := PublicationRecord{
		Version: 1, PostID: "1_200", PageID: "1",
		PublishedAt: time.Now().UTC(),
		Chars:       1300, Bytes: 1720, Opening: "bài mới", HasImage: true,
	}
	for _, r := range []PublicationRecord{older, newer} {
		if err := SavePublication(r); err != nil {
			t.Fatalf("save %s: %v", r.PostID, err)
		}
	}

	got, err := LoadPublications()
	if err != nil {
		t.Fatalf("load: %v", err)
	}
	if len(got) != 2 {
		t.Fatalf("loaded %d records, want 2", len(got))
	}
	// Newest first: a report is read top-down and the recent post is the one
	// whose numbers are still moving.
	if got[0].PostID != "1_200" {
		t.Errorf("first record = %s, want the newest (1_200)", got[0].PostID)
	}
	if !got[0].HasImage || got[0].Bytes != 1720 {
		t.Errorf("record did not round-trip: %+v", got[0])
	}
}

// Samples accumulate rather than overwrite: a post's first day and its third
// say different things, and collapsing them hides which one moved.
func TestPublicationRecord_SamplesAccumulate(t *testing.T) {
	t.Setenv("GOCLAW_DATA_DIR", t.TempDir())

	rec := PublicationRecord{Version: 1, PostID: "1_300", PublishedAt: time.Now().UTC()}
	if err := SavePublication(rec); err != nil {
		t.Fatalf("save: %v", err)
	}
	for i, s := range []PublicationSample{
		{At: time.Now().UTC(), AgeHours: 24, Reactions: 3},
		{At: time.Now().UTC(), AgeHours: 72, Reactions: 11, Comments: 2},
	} {
		rec.Samples = append(rec.Samples, s)
		if err := SavePublication(rec); err != nil {
			t.Fatalf("save sample %d: %v", i, err)
		}
	}

	got, err := LoadPublications()
	if err != nil || len(got) != 1 {
		t.Fatalf("load: %v (%d records)", err, len(got))
	}
	if len(got[0].Samples) != 2 {
		t.Fatalf("samples = %d, want 2 kept as a series", len(got[0].Samples))
	}
	if got[0].Samples[1].Reactions != 11 || got[0].Samples[0].Reactions != 3 {
		t.Errorf("samples lost their history: %+v", got[0].Samples)
	}
}

func TestSavePublication_RequiresPostID(t *testing.T) {
	t.Setenv("GOCLAW_DATA_DIR", t.TempDir())
	err := SavePublication(PublicationRecord{Version: 1})
	if err == nil {
		t.Fatal("saved a record with no post_id; it would be unreadable and unfetchable")
	}
	if !strings.Contains(err.Error(), "post_id") {
		t.Errorf("error should name the missing field: %v", err)
	}
}

// Missing directory is the normal state before the first publish, not an error.
func TestLoadPublications_EmptyWhenNothingPublished(t *testing.T) {
	t.Setenv("GOCLAW_DATA_DIR", t.TempDir())
	got, err := LoadPublications()
	if err != nil {
		t.Fatalf("load on a fresh install must not error: %v", err)
	}
	if len(got) != 0 {
		t.Errorf("got %d records, want none", len(got))
	}
}
