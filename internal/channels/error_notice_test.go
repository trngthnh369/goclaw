package channels

import "testing"

func TestResolveErrorNoticeLocale(t *testing.T) {
	tests := []struct {
		setting string
		want    string
	}{
		{"", ""},
		{"off", ""},
		{"OFF", ""},
		{"vi", "vi"},
		{" EN ", "en"},
		{"zh", "zh"},
		{"fr", ""}, // unsupported locale must not fall back to English silently
		{"true", ""},
	}
	for _, tt := range tests {
		if got := ResolveErrorNoticeLocale(tt.setting); got != tt.want {
			t.Errorf("ResolveErrorNoticeLocale(%q) = %q, want %q", tt.setting, got, tt.want)
		}
	}
}
