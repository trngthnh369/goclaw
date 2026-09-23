package mcp

import (
	"context"
	"slices"
	"testing"
)

func TestResolveEnvVars(t *testing.T) {
	t.Setenv("HOME", "/home/testuser")
	t.Setenv("USER", "testuser")

	tests := []struct {
		name    string
		input   map[string]string
		want    map[string]string
		wantErr bool
	}{
		{
			name:    "resolves allowed env prefix",
			input:   map[string]string{"X-User": "env:USER", "X-Custom": "literal"},
			want:    map[string]string{"X-User": "testuser", "X-Custom": "literal"},
			wantErr: false,
		},
		{
			name:    "resolves HOME env var",
			input:   map[string]string{"X-Home": "env:HOME"},
			want:    map[string]string{"X-Home": "/home/testuser"},
			wantErr: false,
		},
		{
			name:    "nil map",
			input:   nil,
			want:    map[string]string{},
			wantErr: false,
		},
		{
			name:    "rejects non-allowlisted env var",
			input:   map[string]string{"Authorization": "env:AWS_SECRET_KEY"},
			wantErr: true,
		},
		{
			name:    "rejects sensitive env var",
			input:   map[string]string{"X-Token": "env:DATABASE_PASSWORD"},
			wantErr: true,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, err := resolveEnvVars(tt.input)
			if tt.wantErr {
				if err == nil {
					t.Error("expected error, got nil")
				}
				return
			}
			if err != nil {
				t.Errorf("unexpected error: %v", err)
				return
			}
			for k, v := range tt.want {
				if got[k] != v {
					t.Errorf("key %q: got %q, want %q", k, got[k], v)
				}
			}
		})
	}
}

// Regression: mcp-go's default stdio spawn inherited the whole gateway env, so
// an npx-launched MCP server could read the master encryption key.
func TestScrubbedStdioCommand_DropsGatewaySecretsKeepsConfiguredEnv(t *testing.T) {
	t.Setenv("GOCLAW_ENCRYPTION_KEY", "0123"+"abcd")
	t.Setenv("PATH", "/usr/bin")

	configured := []string{"SERVER_API_KEY=" + "set-by-admin"}
	cmd, err := scrubbedStdioCommand(context.Background(), "npx", configured, []string{"-y", "pkg"})
	if err != nil {
		t.Fatalf("scrubbedStdioCommand: %v", err)
	}
	if slices.ContainsFunc(cmd.Env, func(kv string) bool { return len(kv) > 22 && kv[:22] == "GOCLAW_ENCRYPTION_KEY=" }) {
		t.Errorf("GOCLAW_ENCRYPTION_KEY leaked into MCP server env: %v", cmd.Env)
	}
	if !slices.Contains(cmd.Env, "PATH=/usr/bin") {
		t.Errorf("PATH must be inherited, got %v", cmd.Env)
	}
	if !slices.Contains(cmd.Env, configured[0]) {
		t.Errorf("admin-configured env must be kept, got %v", cmd.Env)
	}
	if want := []string{"npx", "-y", "pkg"}; !slices.Equal(cmd.Args, want) {
		t.Errorf("Args = %v, want %v", cmd.Args, want)
	}
}
