package http

import (
	"testing"
)

func TestFilterAllowedKeys_ChannelInstanceIdentityIsImmutable(t *testing.T) {
	input := map[string]any{
		"name":         "renamed-bot",
		"channel_type": "discord",
		"display_name": "Renamed Bot",
	}
	result := filterAllowedKeys(input, channelInstanceAllowedFields)
	if _, ok := result["name"]; ok {
		t.Error("channel instance name must be immutable")
	}
	if _, ok := result["channel_type"]; ok {
		t.Error("channel type must be immutable")
	}
	if result["display_name"] != "Renamed Bot" {
		t.Error("display_name should remain updateable")
	}
}

func TestFilterAllowedKeys(t *testing.T) {
	allowed := map[string]bool{"name": true, "status": true}

	tests := []struct {
		name     string
		updates  map[string]any
		wantKeys []string
	}{
		{
			name:     "keeps allowed keys",
			updates:  map[string]any{"name": "foo", "status": "active"},
			wantKeys: []string{"name", "status"},
		},
		{
			name:     "filters disallowed keys",
			updates:  map[string]any{"name": "foo", "id": "inject", "owner_id": "hack"},
			wantKeys: []string{"name"},
		},
		{
			name:     "empty input returns empty",
			updates:  map[string]any{},
			wantKeys: nil,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := filterAllowedKeys(tt.updates, allowed)
			if tt.wantKeys == nil {
				if len(result) != 0 {
					t.Errorf("expected empty map, got %v", result)
				}
				return
			}
			if len(result) != len(tt.wantKeys) {
				t.Errorf("expected %d keys, got %d: %v", len(tt.wantKeys), len(result), result)
			}
			for _, k := range tt.wantKeys {
				if _, ok := result[k]; !ok {
					t.Errorf("expected key %q in result", k)
				}
			}
		})
	}
}
