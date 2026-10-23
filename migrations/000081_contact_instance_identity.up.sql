DROP INDEX IF EXISTS idx_channel_contacts_tenant_type_sender;

CREATE UNIQUE INDEX idx_channel_contacts_tenant_type_sender
    ON channel_contacts(
        tenant_id,
        channel_type,
        COALESCE(channel_instance, ''),
        sender_id,
        COALESCE(thread_id, '')
    );
