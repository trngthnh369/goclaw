DROP INDEX IF EXISTS idx_channel_contacts_tenant_type_sender;

-- Rolling back the instance-scoped identity is unsafe once multiple instances
-- have contacts with the same legacy key. Fail closed and require an operator
-- to reconcile/export those rows instead of silently deleting merged identities.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM channel_contacts
        GROUP BY tenant_id, channel_type, sender_id, COALESCE(thread_id, '')
        HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION 'cannot rollback migration 81: instance-scoped contact conflicts require manual reconciliation';
    END IF;
END $$;

CREATE UNIQUE INDEX idx_channel_contacts_tenant_type_sender
    ON channel_contacts(
        tenant_id,
        channel_type,
        sender_id,
        COALESCE(thread_id, '')
    );
