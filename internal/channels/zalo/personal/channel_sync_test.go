package personal

import (
	"context"
	"testing"

	"github.com/google/uuid"

	"github.com/nextlevelbuilder/goclaw/internal/cache"
	"github.com/nextlevelbuilder/goclaw/internal/channels"
	"github.com/nextlevelbuilder/goclaw/internal/channels/zalo/personal/protocol"
	"github.com/nextlevelbuilder/goclaw/internal/store"
)

type recordingContactStore struct {
	upserts []recordedContactUpsert
}

type recordedContactUpsert struct {
	tenantID        uuid.UUID
	channelInstance string
	senderID        string
	displayName     string
}

func (s *recordingContactStore) UpsertContact(ctx context.Context, _, channelInstance, senderID, _, displayName, _, _, _, _, _ string) error {
	s.upserts = append(s.upserts, recordedContactUpsert{
		tenantID:        store.TenantIDFromContext(ctx),
		channelInstance: channelInstance,
		senderID:        senderID,
		displayName:     displayName,
	})
	return nil
}
func (s *recordingContactStore) ListContacts(context.Context, store.ContactListOpts) ([]store.ChannelContact, error) {
	return nil, nil
}
func (s *recordingContactStore) CountContacts(context.Context, store.ContactListOpts) (int, error) {
	return 0, nil
}
func (s *recordingContactStore) GetContactsBySenderIDs(context.Context, []string, string) (map[string]store.ChannelContact, error) {
	return nil, nil
}
func (s *recordingContactStore) GetContactByID(context.Context, uuid.UUID) (*store.ChannelContact, error) {
	return nil, nil
}
func (s *recordingContactStore) GetSenderIDsByContactIDs(context.Context, []uuid.UUID) ([]string, error) {
	return nil, nil
}
func (s *recordingContactStore) MergeContacts(context.Context, []uuid.UUID, uuid.UUID) error {
	return nil
}
func (s *recordingContactStore) UnmergeContacts(context.Context, []uuid.UUID) error { return nil }
func (s *recordingContactStore) GetContactsByMergedID(context.Context, uuid.UUID) ([]store.ChannelContact, error) {
	return nil, nil
}
func (s *recordingContactStore) ResolveTenantUserID(context.Context, string, string, string) (string, error) {
	return "", nil
}

func TestSyncGroupContactsRefreshesNameAfterEmptyMessageContact(t *testing.T) {
	tenantID := uuid.New()
	ctx := store.WithTenantID(context.Background(), tenantID)
	contactStore := &recordingContactStore{}
	collector := store.NewContactCollector(contactStore, cache.NewInMemoryCache[bool]())

	base := channels.NewBaseChannel(channels.TypeZaloPersonal, nil, nil)
	base.SetTenantID(tenantID)
	base.SetName("zalo-main")
	base.SetType(channels.TypeZaloPersonal)
	base.SetContactCollector(collector)

	const groupID = "8709947833571143663"
	channel := &Channel{
		BaseChannel: base,
		fetchGroups: func(context.Context, *protocol.Session) ([]protocol.GroupListInfo, error) {
			return []protocol.GroupListInfo{{GroupID: groupID, Name: "TEAM AI"}}, nil
		},
	}

	collector.EnsureContact(ctx, channel.Type(), channel.Name(), groupID, "", "", "", "group", "group", "", "")
	channel.syncGroupContacts(context.Background(), &protocol.Session{})

	if len(contactStore.upserts) != 2 {
		t.Fatalf("upserts = %d, want 2", len(contactStore.upserts))
	}
	got := contactStore.upserts[1]
	if got.displayName != "TEAM AI" {
		t.Errorf("display name = %q, want TEAM AI", got.displayName)
	}
	if got.channelInstance != "zalo-main" {
		t.Errorf("channel instance = %q, want zalo-main", got.channelInstance)
	}
	if got.tenantID != tenantID {
		t.Errorf("tenant ID = %s, want %s", got.tenantID, tenantID)
	}
}
