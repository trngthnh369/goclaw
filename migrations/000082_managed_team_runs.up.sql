ALTER TABLE team_tasks
  ADD COLUMN IF NOT EXISTS batch_id UUID,
  ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(120) NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS task_role VARCHAR(60) NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS dependency_policy VARCHAR(30) NOT NULL DEFAULT 'terminal',
  ADD COLUMN IF NOT EXISTS execution_mode VARCHAR(30) NOT NULL DEFAULT '';

CREATE UNIQUE INDEX IF NOT EXISTS idx_team_tasks_batch_idempotency
  ON team_tasks(tenant_id, team_id, batch_id, idempotency_key)
  WHERE batch_id IS NOT NULL AND idempotency_key <> '';

CREATE INDEX IF NOT EXISTS idx_team_tasks_batch_status
  ON team_tasks(tenant_id, team_id, batch_id, status)
  WHERE batch_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_team_tasks_execution_mode
  ON team_tasks(tenant_id, execution_mode)
  WHERE execution_mode <> '';

CREATE TABLE IF NOT EXISTS publication_slots (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    workflow_key        VARCHAR(120) NOT NULL,
    publication_date    DATE NOT NULL,
    current_generation  INT NOT NULL DEFAULT 1,
    status              VARCHAR(30) NOT NULL DEFAULT 'open',
    metadata            JSONB NOT NULL DEFAULT '{}',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, workflow_key, publication_date)
);

CREATE TABLE IF NOT EXISTS team_task_batches (
    id                    UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id             UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    slot_id               UUID REFERENCES publication_slots(id) ON DELETE CASCADE,
    team_id               UUID NOT NULL REFERENCES agent_teams(id) ON DELETE CASCADE,
    batch_key             VARCHAR(180) NOT NULL,
    generation            INT NOT NULL DEFAULT 1,
    status                VARCHAR(30) NOT NULL DEFAULT 'constructing',
    occurrence_id         VARCHAR(240) NOT NULL DEFAULT '',
    attempt_id            VARCHAR(260) NOT NULL DEFAULT '',
    collector_run_id      VARCHAR(160) NOT NULL DEFAULT '',
    collector_run_path    TEXT NOT NULL DEFAULT '',
    collector_manifest_sha256 VARCHAR(64) NOT NULL DEFAULT '',
    collector_config_hash VARCHAR(64) NOT NULL DEFAULT '',
    collector_committed_at TIMESTAMPTZ,
    publish_not_after     TIMESTAMPTZ,
    batch_deadline        TIMESTAMPTZ,
    destination           JSONB NOT NULL DEFAULT '{}',
    quiesce_generation    INT NOT NULL DEFAULT 1,
    failure_reason        TEXT NOT NULL DEFAULT '',
    metadata              JSONB NOT NULL DEFAULT '{}',
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, team_id, batch_key)
);

CREATE INDEX IF NOT EXISTS idx_team_task_batches_status
  ON team_task_batches(tenant_id, status, batch_deadline);

CREATE INDEX IF NOT EXISTS idx_team_task_batches_team_status
  ON team_task_batches(tenant_id, team_id, status);

CREATE TABLE IF NOT EXISTS publication_deliveries (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    batch_id        UUID NOT NULL REFERENCES team_task_batches(id) ON DELETE CASCADE,
    destination     JSONB NOT NULL DEFAULT '{}',
    artifact_sha256 VARCHAR(64) NOT NULL,
    status          VARCHAR(30) NOT NULL DEFAULT 'planned',
    error           TEXT NOT NULL DEFAULT '',
    lease_owner     VARCHAR(160) NOT NULL DEFAULT '',
    lease_expires_at TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, batch_id, artifact_sha256)
);

CREATE TABLE IF NOT EXISTS publication_delivery_chunks (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    delivery_id     UUID NOT NULL REFERENCES publication_deliveries(id) ON DELETE CASCADE,
    chunk_index     INT NOT NULL,
    content_sha256  VARCHAR(64) NOT NULL,
    status          VARCHAR(30) NOT NULL DEFAULT 'planned',
    discord_message_id VARCHAR(120) NOT NULL DEFAULT '',
    error           TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, delivery_id, chunk_index)
);
