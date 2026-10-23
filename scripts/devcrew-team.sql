-- DevCrew Team Creation
-- Creates team, members, and agent_links for delegation

-- Variables
-- tenant_id = 0193a5b0-7000-7000-8000-000000000001
-- Atlas  = 019d3efe-48bd-70eb-a345-9e93c04c4ac6
-- Scout  = 019d3efe-491a-7b92-85bc-dd21eb614245
-- Forge  = 019d3efe-4962-73b4-b7df-083ac7166118
-- Sentinel = 019d3efe-49a2-70c8-9295-48b2e2953988
-- Lore   = 019d3efe-49d9-79bd-b75d-dcd33a6e2e28

DO $$
DECLARE
  tid uuid := '0193a5b0-7000-7000-8000-000000000001';
  team_uuid uuid := gen_random_uuid();
  atlas_id uuid := '019d3efe-48bd-70eb-a345-9e93c04c4ac6';
  scout_id uuid := '019d3efe-491a-7b92-85bc-dd21eb614245';
  forge_id uuid := '019d3efe-4962-73b4-b7df-083ac7166118';
  sentinel_id uuid := '019d3efe-49a2-70c8-9295-48b2e2953988';
  lore_id uuid := '019d3efe-49d9-79bd-b75d-dcd33a6e2e28';
BEGIN
  -- 1. Create Team
  INSERT INTO agent_teams (id, name, lead_agent_id, description, status, settings, created_by, tenant_id, created_at, updated_at)
  VALUES (
    team_uuid,
    'DevCrew',
    atlas_id,
    'Software Development Team — Atlas (Lead), Scout (Explorer), Forge (Implementer), Sentinel (Reviewer), Lore (Chronicler)',
    'active',
    '{"workspace_scope": "shared", "progress_notifications": true}'::jsonb,
    'admin',
    tid,
    now(), now()
  );

  RAISE NOTICE 'Created team DevCrew: %', team_uuid;

  -- 2. Add Members
  -- Atlas as Lead
  INSERT INTO agent_team_members (team_id, agent_id, role, joined_at, tenant_id)
  VALUES (team_uuid, atlas_id, 'lead', now(), tid);

  -- Scout as Member
  INSERT INTO agent_team_members (team_id, agent_id, role, joined_at, tenant_id)
  VALUES (team_uuid, scout_id, 'member', now(), tid);

  -- Forge as Member
  INSERT INTO agent_team_members (team_id, agent_id, role, joined_at, tenant_id)
  VALUES (team_uuid, forge_id, 'member', now(), tid);

  -- Sentinel as Member
  INSERT INTO agent_team_members (team_id, agent_id, role, joined_at, tenant_id)
  VALUES (team_uuid, sentinel_id, 'member', now(), tid);

  -- Lore as Member
  INSERT INTO agent_team_members (team_id, agent_id, role, joined_at, tenant_id)
  VALUES (team_uuid, lore_id, 'member', now(), tid);

  RAISE NOTICE 'Added 5 members (1 lead + 4 members)';

  -- 3. Create Agent Links (Atlas → each member for delegation)
  -- Atlas → Scout
  INSERT INTO agent_links (id, source_agent_id, target_agent_id, direction, description, max_concurrent, settings, status, created_by, team_id, tenant_id, created_at, updated_at)
  VALUES (gen_random_uuid(), atlas_id, scout_id, 'outbound', 'Atlas delegates exploration and spec writing to Scout', 1, '{}'::jsonb, 'active', 'admin', team_uuid, tid, now(), now());

  -- Atlas → Forge
  INSERT INTO agent_links (id, source_agent_id, target_agent_id, direction, description, max_concurrent, settings, status, created_by, team_id, tenant_id, created_at, updated_at)
  VALUES (gen_random_uuid(), atlas_id, forge_id, 'outbound', 'Atlas delegates implementation tasks to Forge', 1, '{}'::jsonb, 'active', 'admin', team_uuid, tid, now(), now());

  -- Atlas → Sentinel
  INSERT INTO agent_links (id, source_agent_id, target_agent_id, direction, description, max_concurrent, settings, status, created_by, team_id, tenant_id, created_at, updated_at)
  VALUES (gen_random_uuid(), atlas_id, sentinel_id, 'outbound', 'Atlas delegates code review and QA to Sentinel', 1, '{}'::jsonb, 'active', 'admin', team_uuid, tid, now(), now());

  -- Atlas → Lore
  INSERT INTO agent_links (id, source_agent_id, target_agent_id, direction, description, max_concurrent, settings, status, created_by, team_id, tenant_id, created_at, updated_at)
  VALUES (gen_random_uuid(), atlas_id, lore_id, 'outbound', 'Atlas delegates documentation and ship checklist to Lore', 1, '{}'::jsonb, 'active', 'admin', team_uuid, tid, now(), now());

  RAISE NOTICE 'Created 4 agent links (Atlas → Scout/Forge/Sentinel/Lore)';
  RAISE NOTICE 'DevCrew setup complete!';
END $$;

-- Verify
SELECT t.id as team_id, t.name, t.status, 
       (SELECT count(*) FROM agent_team_members WHERE team_id = t.id) as member_count
FROM agent_teams t WHERE t.name = 'DevCrew';
