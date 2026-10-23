//go:build sqlite || sqliteonly

package sqlitestore

import (
	"database/sql"
	"os"
	"path/filepath"
	"testing"
)

// TestEnsureSchema_UpgradeV50ToV51 exercises the upgrade path an existing
// desktop database actually takes. Adding the managed-team-run columns to
// schema.sql alone would fix fresh installs and leave every v50 database
// broken, because sqlitestore/teams_tasks.go selects those columns
// unconditionally.
//
// The v50 shape is reconstructed from the pre-migration definition rather than
// from the current schema.sql, so this test keeps failing if patch 50 is ever
// dropped from the migrations map.
func TestEnsureSchema_UpgradeV50ToV51(t *testing.T) {
	dir := t.TempDir()
	dbPath := filepath.Join(dir, "v50.db")

	db, err := sql.Open("sqlite", dbPath)
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	defer db.Close()

	// Minimal v50-shaped subset: the FK targets plus team_tasks WITHOUT the
	// five managed-run columns.
	const v50Schema = `
CREATE TABLE tenants (
    id   TEXT NOT NULL PRIMARY KEY,
    name TEXT NOT NULL DEFAULT ''
);
CREATE TABLE agents (
    id TEXT NOT NULL PRIMARY KEY
);
CREATE TABLE agent_teams (
    id        TEXT NOT NULL PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id)
);
CREATE TABLE team_tasks (
    id                   TEXT NOT NULL PRIMARY KEY,
    team_id              TEXT NOT NULL REFERENCES agent_teams(id) ON DELETE CASCADE,
    subject              VARCHAR(500) NOT NULL,
    description          TEXT,
    status               VARCHAR(20) NOT NULL DEFAULT 'pending',
    owner_agent_id       TEXT REFERENCES agents(id) ON DELETE SET NULL,
    blocked_by           TEXT NOT NULL DEFAULT '[]',
    priority             INT NOT NULL DEFAULT 0,
    result               TEXT,
    metadata             TEXT NOT NULL DEFAULT '{}',
    custom_scope         TEXT,
    tenant_id            TEXT NOT NULL REFERENCES tenants(id),
    created_at           TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at           TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE TABLE schema_version (version INT NOT NULL);
INSERT INTO schema_version (version) VALUES (50);
`
	if _, err := db.Exec(v50Schema); err != nil {
		t.Fatalf("seed v50 schema: %v", err)
	}

	if err := EnsureSchema(db); err != nil {
		t.Fatalf("EnsureSchema v50 to v51: %v", err)
	}

	for _, col := range []string{"batch_id", "idempotency_key", "task_role", "dependency_policy", "execution_mode"} {
		var n int
		if err := db.QueryRow(
			`SELECT COUNT(*) FROM pragma_table_info('team_tasks') WHERE name = ?`, col,
		).Scan(&n); err != nil {
			t.Fatalf("pragma_table_info(%s): %v", col, err)
		}
		if n != 1 {
			t.Errorf("team_tasks.%s missing after upgrade — desktop team queries would fail with 'no such column'", col)
		}
	}

	for _, tbl := range []string{
		"publication_slots", "team_task_batches",
		"publication_deliveries", "publication_delivery_chunks",
	} {
		var n int
		if err := db.QueryRow(
			`SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = ?`, tbl,
		).Scan(&n); err != nil {
			t.Fatalf("sqlite_master(%s): %v", tbl, err)
		}
		if n != 1 {
			t.Errorf("table %s missing after upgrade", tbl)
		}
	}

	var version int
	if err := db.QueryRow(`SELECT version FROM schema_version`).Scan(&version); err != nil {
		t.Fatalf("read schema_version: %v", err)
	}
	if version != SchemaVersion {
		t.Errorf("schema_version = %d, want %d", version, SchemaVersion)
	}

	if _, err := os.Stat(dbPath); err != nil {
		t.Fatalf("db file: %v", err)
	}
}

// TestEnsureSchema_FreshInstallHasManagedRunColumns guards the other half:
// schema.sql (used for brand-new databases) must carry the same columns the
// store layer selects.
func TestEnsureSchema_FreshInstallHasManagedRunColumns(t *testing.T) {
	db, err := sql.Open("sqlite", filepath.Join(t.TempDir(), "fresh.db"))
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	defer db.Close()

	if err := EnsureSchema(db); err != nil {
		t.Fatalf("EnsureSchema fresh: %v", err)
	}

	for _, col := range []string{"batch_id", "idempotency_key", "task_role", "dependency_policy", "execution_mode"} {
		var n int
		if err := db.QueryRow(
			`SELECT COUNT(*) FROM pragma_table_info('team_tasks') WHERE name = ?`, col,
		).Scan(&n); err != nil {
			t.Fatalf("pragma_table_info(%s): %v", col, err)
		}
		if n != 1 {
			t.Errorf("fresh install is missing team_tasks.%s", col)
		}
	}
}
