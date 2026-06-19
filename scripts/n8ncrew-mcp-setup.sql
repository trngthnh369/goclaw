-- N8nCrew MCP Setup: Register n8n-custom-mcp + Grant to Spark & Pulse
-- Prerequisite: Run n8ncrew-setup.sql first if agents don't exist
--
-- Usage:
--   docker cp scripts/n8ncrew-mcp-setup.sql goclaw-postgres-1:/tmp/n8ncrew-mcp.sql
--   docker exec goclaw-postgres-1 psql -U goclaw -d goclaw -f /tmp/n8ncrew-mcp.sql

-- ============================================================
-- Step 1: Verify N8nCrew agents exist
-- ============================================================
DO $$
DECLARE
  cnt int;
BEGIN
  SELECT count(*) INTO cnt FROM agents WHERE agent_key LIKE 'n8ncrew-%';
  IF cnt < 4 THEN
    RAISE EXCEPTION 'N8nCrew agents not found (found %). Run n8ncrew-setup.sql first.', cnt;
  END IF;
  RAISE NOTICE 'N8nCrew agents verified: % found', cnt;
END $$;

-- ============================================================
-- Step 2: Register n8n-custom-mcp server (streamable-http)
-- ============================================================
DO $$
DECLARE
  tid uuid := '0193a5b0-7000-7000-8000-000000000001';
  owner text := 'trngthnh369';
  srv_id uuid;
  spark_id uuid;
  pulse_id uuid;
  conductor_id uuid;
BEGIN
  -- Upsert MCP server
  INSERT INTO mcp_servers (
    name, display_name, transport, url, timeout_sec, settings, enabled, created_by, tenant_id
  ) VALUES (
    'n8n-custom-mcp',
    'n8n Workflow Builder',
    'streamable-http',
    'http://n8n-mcp:3000/mcp',
    120,
    '{"description": "36-tool MCP server for n8n: create, edit, execute, debug, patch, validate workflows autonomously"}'::jsonb,
    true,
    owner,
    tid
  )
  ON CONFLICT (tenant_id, name) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    transport = EXCLUDED.transport,
    url = EXCLUDED.url,
    timeout_sec = EXCLUDED.timeout_sec,
    settings = EXCLUDED.settings,
    enabled = EXCLUDED.enabled,
    updated_at = now()
  RETURNING id INTO srv_id;

  RAISE NOTICE 'MCP server registered: id=%', srv_id;

  -- Lookup agent IDs
  SELECT id INTO spark_id FROM agents WHERE agent_key = 'n8ncrew-spark' AND tenant_id = tid;
  SELECT id INTO pulse_id FROM agents WHERE agent_key = 'n8ncrew-pulse' AND tenant_id = tid;
  SELECT id INTO conductor_id FROM agents WHERE agent_key = 'n8ncrew-conductor' AND tenant_id = tid;

  -- ============================================================
  -- Step 3: Grant MCP access to agents
  -- ============================================================

  -- Spark (builder): all tools — needs full CRUD + deploy
  INSERT INTO mcp_agent_grants (server_id, agent_id, enabled, granted_by, tenant_id)
  VALUES (srv_id, spark_id, true, owner, tid)
  ON CONFLICT (server_id, agent_id) DO UPDATE SET enabled = true;

  RAISE NOTICE 'Granted n8n-mcp → Spark (all tools)';

  -- Pulse (tester): all tools — needs execute + debug + validate
  INSERT INTO mcp_agent_grants (server_id, agent_id, enabled, granted_by, tenant_id)
  VALUES (srv_id, pulse_id, true, owner, tid)
  ON CONFLICT (server_id, agent_id) DO UPDATE SET enabled = true;

  RAISE NOTICE 'Granted n8n-mcp → Pulse (all tools)';

  -- Conductor (architect): read-only — can inspect but not build
  INSERT INTO mcp_agent_grants (
    server_id, agent_id, enabled, tool_allow, granted_by, tenant_id
  ) VALUES (
    srv_id, conductor_id, true,
    '["list_workflows", "get_workflow", "get_workflow_summary", "list_executions", "get_execution", "get_execution_data", "diagnose_workflow", "list_credentials", "list_tags", "search_templates", "get_template", "list_node_types", "get_node_type_details", "get_node_schema", "get_node_versions"]'::jsonb,
    owner, tid
  )
  ON CONFLICT (server_id, agent_id) DO UPDATE SET
    enabled = true,
    tool_allow = EXCLUDED.tool_allow;

  RAISE NOTICE 'Granted n8n-mcp → Conductor (read-only: 15 tools)';
END $$;

-- ============================================================
-- Step 4: Grant n8n-patterns skill to Spark & Conductor
-- ============================================================
DO $$
DECLARE
  tid uuid := '0193a5b0-7000-7000-8000-000000000001';
  owner text := 'trngthnh369';
  skill_uuid uuid;
  spark_id uuid;
  conductor_id uuid;
BEGIN
  SELECT id INTO skill_uuid FROM skills WHERE slug = 'n8n-patterns' AND tenant_id = tid;
  IF skill_uuid IS NULL THEN
    RAISE NOTICE 'Skill n8n-patterns not found in DB — it may auto-load from disk. Skipping grants.';
    RETURN;
  END IF;

  SELECT id INTO spark_id FROM agents WHERE agent_key = 'n8ncrew-spark' AND tenant_id = tid;
  SELECT id INTO conductor_id FROM agents WHERE agent_key = 'n8ncrew-conductor' AND tenant_id = tid;

  INSERT INTO skill_agent_grants (skill_id, agent_id, pinned_version, granted_by, tenant_id)
  VALUES (skill_uuid, spark_id, 1, owner, tid)
  ON CONFLICT (skill_id, agent_id) DO NOTHING;

  INSERT INTO skill_agent_grants (skill_id, agent_id, pinned_version, granted_by, tenant_id)
  VALUES (skill_uuid, conductor_id, 1, owner, tid)
  ON CONFLICT (skill_id, agent_id) DO NOTHING;

  RAISE NOTICE 'Granted n8n-patterns skill → Spark + Conductor';
END $$;

-- ============================================================
-- Verify
-- ============================================================
SELECT '=== MCP SERVER ===' as section;
SELECT name, display_name, transport, url, enabled
FROM mcp_servers WHERE name = 'n8n-custom-mcp';

SELECT '=== MCP GRANTS ===' as section;
SELECT a.agent_key, g.enabled,
  CASE WHEN g.tool_allow IS NULL THEN 'ALL' ELSE g.tool_allow::text END as tools
FROM mcp_agent_grants g
JOIN agents a ON a.id = g.agent_id
JOIN mcp_servers s ON s.id = g.server_id
WHERE s.name = 'n8n-custom-mcp'
ORDER BY a.agent_key;

SELECT '=== SKILL GRANTS ===' as section;
SELECT a.agent_key, s.slug as skill
FROM skill_agent_grants sg
JOIN agents a ON a.id = sg.agent_id
JOIN skills s ON s.id = sg.skill_id
WHERE s.slug = 'n8n-patterns'
ORDER BY a.agent_key;

SELECT '=== N8NCREW TEAM STATUS ===' as section;
SELECT a.agent_key, a.display_name, a.status, a.provider, a.model,
  m.role as team_role
FROM agents a
JOIN agent_team_members m ON m.agent_id = a.id
JOIN agent_teams t ON t.id = m.team_id
WHERE t.name = 'N8nCrew'
ORDER BY m.role, a.agent_key;
