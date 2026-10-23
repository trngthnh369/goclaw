DROP TABLE IF EXISTS publication_delivery_chunks;
DROP TABLE IF EXISTS publication_deliveries;
DROP TABLE IF EXISTS team_task_batches;
DROP TABLE IF EXISTS publication_slots;

DROP INDEX IF EXISTS idx_team_tasks_execution_mode;
DROP INDEX IF EXISTS idx_team_tasks_batch_status;
DROP INDEX IF EXISTS idx_team_tasks_batch_idempotency;

ALTER TABLE team_tasks DROP COLUMN IF EXISTS execution_mode;
ALTER TABLE team_tasks DROP COLUMN IF EXISTS dependency_policy;
ALTER TABLE team_tasks DROP COLUMN IF EXISTS task_role;
ALTER TABLE team_tasks DROP COLUMN IF EXISTS idempotency_key;
ALTER TABLE team_tasks DROP COLUMN IF EXISTS batch_id;
