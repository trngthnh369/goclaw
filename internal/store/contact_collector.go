package store

import (
	"context"
	"log/slog"
	"time"

	"github.com/nextlevelbuilder/goclaw/internal/cache"
)

const contactSeenTTL = 30 * time.Minute

// ContactCollector wraps ContactStore with an in-memory "seen" cache
// to avoid redundant UPSERT queries on every message.
type ContactCollector struct {
	store ContactStore
	seen  cache.Cache[bool]
}

// NewContactCollector creates a new collector backed by the given store and cache.
func NewContactCollector(s ContactStore, c cache.Cache[bool]) *ContactCollector {
	return &ContactCollector{store: s, seen: c}
}

// EnsureContact creates or refreshes a contact entry, skipping DB if recently seen.
// contactType: "user" (individual sender), "group" (group chat entity), or "topic" (forum topic).
// Pass empty threadID/threadType for base contacts (DM, group root).
func (c *ContactCollector) EnsureContact(ctx context.Context, channelType, channelInstance, senderID, userID, displayName, username, peerKind, contactType, threadID, threadType string) {
	key := contactCacheKey(ctx, channelType, channelInstance, senderID, threadID)
	if _, ok := c.seen.Get(ctx, key); ok {
		return
	}
	if err := c.RefreshContact(ctx, channelType, channelInstance, senderID, userID, displayName, username, peerKind, contactType, threadID, threadType); err != nil {
		slog.Warn("contact_collector.upsert_failed",
			"error", err,
			"tenant_id", TenantIDFromContext(ctx),
			"channel", channelType,
			"instance", channelInstance,
			"sender", senderID,
		)
	}
}

// RefreshContact always upserts authoritative contact metadata, bypassing the
// seen cache, then refreshes the cache entry after persistence succeeds.
func (c *ContactCollector) RefreshContact(ctx context.Context, channelType, channelInstance, senderID, userID, displayName, username, peerKind, contactType, threadID, threadType string) error {
	if contactType == "" {
		contactType = "user"
	}
	if err := c.store.UpsertContact(ctx, channelType, channelInstance, senderID, userID, displayName, username, peerKind, contactType, threadID, threadType); err != nil {
		return err
	}
	c.seen.Set(ctx, contactCacheKey(ctx, channelType, channelInstance, senderID, threadID), true, contactSeenTTL)
	return nil
}

func contactCacheKey(ctx context.Context, channelType, channelInstance, senderID, threadID string) string {
	// Keep this aligned with the DB unique identity. Zero tenant UUID preserves
	// Desktop/single-tenant behavior while channelInstance isolates bot accounts.
	tid := TenantIDFromContext(ctx)
	return tid.String() + ":" + channelType + ":" + channelInstance + ":" + senderID + ":" + threadID
}

// ResolveTenantUserID delegates to the underlying ContactStore.
func (c *ContactCollector) ResolveTenantUserID(ctx context.Context, channelType, channelInstance, senderID string) (string, error) {
	return c.store.ResolveTenantUserID(ctx, channelType, channelInstance, senderID)
}
