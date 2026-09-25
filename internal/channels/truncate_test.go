package channels

import (
	"strings"
	"testing"
	"unicode/utf8"
)

func TestTruncateNeverSplitsARune(t *testing.T) {
	// "ệ" is 3 bytes: every cut inside it must fall back to the rune start.
	s := strings.Repeat("a", 498) + "ệt nam"
	for maxLen := 495; maxLen <= 505; maxLen++ {
		got := Truncate(s, maxLen)
		if !utf8.ValidString(got) {
			t.Fatalf("Truncate(_, %d) returned invalid UTF-8: % x", maxLen, got[len(got)-6:])
		}
		if !strings.HasSuffix(got, "...") || len(got)-3 > maxLen {
			t.Fatalf("Truncate(_, %d) = %d bytes before the ellipsis", maxLen, len(got)-3)
		}
	}
}

func TestTruncateKeepsShortStringsAndHandlesZero(t *testing.T) {
	if got := Truncate("duyệt", 50); got != "duyệt" {
		t.Fatalf("short string changed: %q", got)
	}
	if got := Truncate("ệ", 0); got != "..." {
		t.Fatalf("zero length: %q", got)
	}
}
