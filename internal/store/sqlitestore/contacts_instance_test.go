//go:build sqlite || sqliteonly

package sqlitestore

import (
	"context"
	"database/sql"
	"testing"

	"github.com/google/uuid"

	"github.com/nextlevelbuilder/goclaw/internal/store"
)

func TestSQLiteContactSchema_IsolatesChannelInstances(t *testing.T) {
	db := openTestDB(t)
	if err := EnsureSchema(db); err != nil {
		t.Fatalf("EnsureSchema: %v", err)
	}
	assertContactInstancesAreIsolated(t, db)
}

func TestSQLiteContactSchema_Migration49IsolatesChannelInstances(t *testing.T) {
	db := openTestDBAtVersion(t, 49)
	if err := EnsureSchema(db); err != nil {
		t.Fatalf("EnsureSchema v49→v50: %v", err)
	}
	assertContactInstancesAreIsolated(t, db)
}

func TestSQLiteContactStore_UpsertContactIsolatesChannelInstances(t *testing.T) {
	db := openTestDB(t)
	if err := EnsureSchema(db); err != nil {
		t.Fatalf("EnsureSchema: %v", err)
	}

	contactStore := NewSQLiteContactStore(db)
	ctx := store.WithTenantID(context.Background(), store.MasterTenantID)
	const groupID = "8709947833571143663"

	for _, instance := range []string{"zalo-a", "zalo-b"} {
		if err := contactStore.UpsertContact(ctx, "zalo_personal", instance, groupID, "", "TEAM AI", "", "group", "group", "", ""); err != nil {
			t.Fatalf("upsert %s: %v", instance, err)
		}
	}

	for _, instance := range []string{"zalo-a", "zalo-b"} {
		var count int
		err := db.QueryRow(`SELECT COUNT(*) FROM channel_contacts
			WHERE tenant_id = ? AND channel_type = 'zalo_personal'
			AND channel_instance = ? AND sender_id = ?`,
			store.MasterTenantID.String(), instance, groupID,
		).Scan(&count)
		if err != nil {
			t.Fatalf("count %s: %v", instance, err)
		}
		if count != 1 {
			t.Fatalf("instance %s contacts = %d, want 1", instance, count)
		}
	}
}

func assertContactInstancesAreIsolated(t *testing.T, db *sql.DB) {
	t.Helper()
	tenantID := store.MasterTenantID.String()
	const groupID = "8709947833571143663"
	insert := `INSERT INTO channel_contacts (
		id, channel_type, channel_instance, sender_id, display_name,
		peer_kind, contact_type, tenant_id
	) VALUES (?, 'zalo_personal', ?, ?, ?, 'group', 'group', ?)`

	if _, err := db.Exec(insert, uuid.NewString(), "zalo-a", groupID, "Team AI A", tenantID); err != nil {
		t.Fatalf("insert instance A: %v", err)
	}
	if _, err := db.Exec(insert, uuid.NewString(), "zalo-b", groupID, "Team AI B", tenantID); err != nil {
		t.Fatalf("insert instance B: %v", err)
	}

	for _, instance := range []string{"zalo-a", "zalo-b"} {
		var count int
		err := db.QueryRow(`SELECT COUNT(*) FROM channel_contacts
			WHERE tenant_id = ? AND channel_type = 'zalo_personal'
			AND channel_instance = ? AND sender_id = ?`,
			tenantID, instance, groupID,
		).Scan(&count)
		if err != nil {
			t.Fatalf("count %s: %v", instance, err)
		}
		if count != 1 {
			t.Errorf("instance %s contact count = %d, want 1", instance, count)
		}
	}
}
