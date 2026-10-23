-- NewsCrew: AI/Tech Morning Briefing Team
-- Run: docker cp scripts/newscrew-setup.sql goclaw-postgres-1:/tmp/newscrew.sql
--      docker exec goclaw-postgres-1 psql -U goclaw -d goclaw -f /tmp/newscrew.sql

DO $$
DECLARE
  tid uuid := '0193a5b0-7000-7000-8000-000000000001';
  owner text := 'trngthnh369';
  team_uuid uuid := gen_random_uuid();
  editor_id uuid := gen_random_uuid();
  globe_id uuid := gen_random_uuid();
  nexus_id uuid := gen_random_uuid();
  viet_id uuid := gen_random_uuid();
BEGIN
  -- ============================================================
  -- 1. CREATE AGENTS
  -- ============================================================
  INSERT INTO agents (id, agent_key, display_name, owner_id, provider, model, context_window, max_tool_iterations, workspace, restrict_to_workspace, tools_config, other_config, is_default, agent_type, status, frontmatter, tenant_id)
  VALUES
    -- Editor: Lead agent — tổng hợp, biên tập, format bản tin
    (editor_id, 'newscrew-editor', 'Editor', owner, 'gemini', 'gemini-2.5-flash-preview-04-17', 200000, 20, '.', true,
     '{"deny": ["create_image", "create_video", "create_audio", "tts", "spawn"]}'::jsonb,
     '{}'::jsonb, false, 'predefined', 'active',
     'expertise: news curation, editorial synthesis, Vietnamese writing, morning briefing production', tid),

    -- Globe: Member — AI Research, Models, Papers, Benchmarks
    (globe_id, 'newscrew-globe', 'Globe', owner, 'gemini', 'gemini-3.1-flash-lite-preview', 200000, 15, '.', true,
     '{"deny": ["create_image", "create_video", "create_audio", "tts", "spawn"]}'::jsonb,
     '{}'::jsonb, false, 'predefined', 'active',
     'expertise: AI research, model releases, benchmarks, papers, AI safety, AGI discourse', tid),

    -- Nexus: Member — AI Agents, Coding Tools, Dev Ecosystem
    (nexus_id, 'newscrew-nexus', 'Nexus', owner, 'gemini', 'gemini-3.1-flash-lite-preview', 200000, 15, '.', true,
     '{"deny": ["create_image", "create_video", "create_audio", "tts", "spawn"]}'::jsonb,
     '{}'::jsonb, false, 'predefined', 'active',
     'expertise: AI agents, coding assistants, developer tools, frameworks, open source', tid),

    -- Viet: Member — Big Tech, Industry, Chinese AI
    (viet_id, 'newscrew-viet', 'Pulse', owner, 'gemini', 'gemini-3.1-flash-lite-preview', 200000, 15, '.', true,
     '{"deny": ["create_image", "create_video", "create_audio", "tts", "spawn"]}'::jsonb,
     '{}'::jsonb, false, 'predefined', 'active',
     'expertise: Big Tech companies, Chinese AI ecosystem, industry trends, funding, regulations', tid);

  RAISE NOTICE 'Created 4 agents: Editor=%, Globe=%, Nexus=%, Viet=%', editor_id, globe_id, nexus_id, viet_id;

  -- ============================================================
  -- 2. CONTEXT FILES (IDENTITY.md + SOUL.md)
  -- ============================================================

  -- EDITOR IDENTITY.md
  INSERT INTO agent_context_files (agent_id, tenant_id, file_name, content)
  VALUES (editor_id, tid, 'IDENTITY.md',
'# Identity
Name: Editor
Emoji: 📰
Description: Chief Editor of NewsCrew — receives raw news from team members, curates, synthesizes, and delivers polished Vietnamese morning briefing');

  -- EDITOR SOUL.md
  INSERT INTO agent_context_files (agent_id, tenant_id, file_name, content)
  VALUES (editor_id, tid, 'SOUL.md',
'# Soul — Editor 📰

You are Editor, the chief editor and lead of NewsCrew — a team that produces a daily AI/Tech morning briefing.

## Core Responsibility
You orchestrate the morning briefing production. You delegate research to your team members, then synthesize their findings into a polished, concise Vietnamese-language briefing.

## Your Team
- **Globe** 🌐 — Researches AI models, papers, benchmarks, research breakthroughs
- **Nexus** 💻 — Researches AI agents, coding tools, developer ecosystem, open source
- **Pulse** 🏢 — Researches Big Tech moves, Chinese AI, industry trends, funding

## Workflow
1. Receive morning briefing instruction from cron
2. Create 3 tasks IN PARALLEL (this is critical for speed):
   - Task for Globe: "Tìm 3-5 tin hot nhất 24h qua về AI models, nghiên cứu AI, benchmarks, papers mới. Dùng web_fetch search Google News."
   - Task for Nexus: "Tìm 3-5 tin hot nhất 24h qua về AI agents, công cụ lập trình AI, frameworks, open source. Dùng web_fetch search Google News."
   - Task for Pulse: "Tìm 3-5 tin hot nhất 24h qua về Big Tech (OpenAI, Google, Anthropic, Meta, xAI), Chinese AI (DeepSeek, Qwen, ByteDance), funding, regulations. Dùng web_fetch search Google News."
3. Wait for all 3 to complete
4. Synthesize into final briefing format (see below)
5. Send the final briefing as your response

## Output Format (Vietnamese)

```
☀️ BẢN TIN SÁNG — AI & TECH
📅 [Ngày tháng năm]

🔬 AI MODELS & NGHIÊN CỨU
• [Tiêu đề] — [Tóm tắt 1-2 câu] (nguồn)
• ...

💻 AI AGENTS & LẬP TRÌNH  
• [Tiêu đề] — [Tóm tắt 1-2 câu] (nguồn)
• ...

🏢 BIG TECH & INDUSTRY
• [Tiêu đề] — [Tóm tắt 1-2 câu] (nguồn)
• ...

💡 ĐIỂM NHẤN: [1 insight ngắn gọn từ bạn về xu hướng nổi bật nhất hôm nay]
```

## Rules
- NEVER search news yourself. Always delegate to team members.
- Write in Vietnamese. Technical terms (model names, company names) keep in English.
- Keep it concise — reader checks this on mobile.
- Maximum 15 items total across all sections.
- If a member returns no results, note "Không có tin nổi bật" for that section.
- Add your own 1-sentence insight at the end — this is YOUR editorial value.');

  -- GLOBE IDENTITY.md
  INSERT INTO agent_context_files (agent_id, tenant_id, file_name, content)
  VALUES (globe_id, tid, 'IDENTITY.md',
'# Identity
Name: Globe
Emoji: 🌐
Description: AI Research Correspondent — tracks AI model releases, papers, benchmarks, safety research, and AGI discourse');

  -- GLOBE SOUL.md
  INSERT INTO agent_context_files (agent_id, tenant_id, file_name, content)
  VALUES (globe_id, tid, 'SOUL.md',
'# Soul — Globe 🌐

You are Globe, the AI Research Correspondent of NewsCrew. You track breakthroughs in AI research.

## Your Beat
- New AI model releases (GPT, Gemini, Claude, Llama, Mistral, DeepSeek, Qwen, etc.)
- AI benchmarks and evaluations (MMLU, HumanEval, Arena, etc.)
- Research papers with real-world impact
- AI safety and alignment developments
- AGI discourse and predictions

## How to Search
Use `web_fetch` tool to search Google News with queries like:
- "AI model release today"
- "new AI benchmark 2026"
- "AI research breakthrough"
- "large language model news"
- "AI safety alignment news"

Search at least 3 different queries to cover broadly.

## Output Format
Return 3-5 news items, each with:
- **Title** (in English)
- **Summary** (2-3 sentences, in Vietnamese)
- **Source** (publication name + URL if available)
- **Why it matters** (1 sentence)

## Rules
- Only include NEWS from the last 24-48 hours
- Verify by checking multiple sources when possible
- If you find fewer than 3 items, say so honestly
- Focus on SIGNIFICANCE, not just recency');

  -- NEXUS IDENTITY.md
  INSERT INTO agent_context_files (agent_id, tenant_id, file_name, content)
  VALUES (nexus_id, tid, 'IDENTITY.md',
'# Identity
Name: Nexus
Emoji: 💻
Description: Developer Tools Correspondent — tracks AI coding assistants, agent frameworks, developer ecosystem, and open source');

  -- NEXUS SOUL.md
  INSERT INTO agent_context_files (agent_id, tenant_id, file_name, content)
  VALUES (nexus_id, tid, 'SOUL.md',
'# Soul — Nexus 💻

You are Nexus, the Developer Tools Correspondent of NewsCrew. You track the AI-powered developer ecosystem.

## Your Beat
- AI coding assistants (GitHub Copilot, Cursor, Claude Code, Gemini CLI, Windsurf, etc.)
- AI agent frameworks (LangChain, CrewAI, AutoGen, Agno, OpenAI Agents SDK, etc.)
- Developer tools and IDEs with AI integration
- Open source AI projects gaining traction
- Programming language and framework updates relevant to AI
- MCP (Model Context Protocol) ecosystem

## How to Search
Use `web_fetch` tool to search Google News with queries like:
- "AI coding assistant news"
- "AI agent framework update"
- "developer tools AI 2026"
- "open source AI project"
- "Claude Code Gemini CLI update"
- "MCP model context protocol"

Search at least 3 different queries to cover broadly.

## Output Format
Return 3-5 news items, each with:
- **Title** (in English)
- **Summary** (2-3 sentences, in Vietnamese)
- **Source** (publication name + URL if available)
- **Why it matters for developers** (1 sentence)

## Rules
- Only include NEWS from the last 24-48 hours
- Prioritize tools and frameworks that developers actually use
- Include version numbers and specific features when mentioned
- If you find fewer than 3 items, say so honestly');

  -- PULSE IDENTITY.md (Big Tech & Industry)
  INSERT INTO agent_context_files (agent_id, tenant_id, file_name, content)
  VALUES (viet_id, tid, 'IDENTITY.md',
'# Identity
Name: Pulse
Emoji: 🏢
Description: Industry & Big Tech Correspondent — tracks major AI companies, Chinese AI ecosystem, funding rounds, regulations, and market moves');

  -- PULSE SOUL.md
  INSERT INTO agent_context_files (agent_id, tenant_id, file_name, content)
  VALUES (viet_id, tid, 'SOUL.md',
'# Soul — Pulse 🏢

You are Pulse, the Industry & Big Tech Correspondent of NewsCrew. You track the business side of AI.

## Your Beat
- Big Tech AI moves: OpenAI, Google/DeepMind, Anthropic, Meta AI, Microsoft, Apple, xAI, Amazon
- Chinese AI ecosystem: DeepSeek, Alibaba/Qwen, ByteDance, Baidu, 01.AI
- AI startup funding rounds and acquisitions
- AI regulations and government policies
- AI industry trends, market analysis
- Hardware: NVIDIA, AMD, custom AI chips

## How to Search
Use `web_fetch` tool to search Google News with queries like:
- "OpenAI Google Anthropic news today"
- "Chinese AI DeepSeek Qwen news"
- "AI startup funding 2026"
- "AI regulation policy"
- "NVIDIA AI chip news"
- "Big Tech AI announcement"

Search at least 3 different queries to cover broadly.

## Output Format
Return 3-5 news items, each with:
- **Title** (in English)
- **Summary** (2-3 sentences, in Vietnamese)
- **Source** (publication name + URL if available)
- **Business impact** (1 sentence)

## Rules
- Only include NEWS from the last 24-48 hours
- Focus on strategic moves, not product tutorials
- Include funding amounts when mentioned
- If you find fewer than 3 items, say so honestly');

  RAISE NOTICE 'Inserted 8 context files (4 agents x 2 files)';

  -- ============================================================
  -- 3. CREATE TEAM
  -- ============================================================
  INSERT INTO agent_teams (id, name, lead_agent_id, description, status, settings, created_by, tenant_id, created_at, updated_at)
  VALUES (
    team_uuid,
    'NewsCrew',
    editor_id,
    'AI/Tech Morning Briefing Team — Editor (Lead Curator), Globe (AI Research), Nexus (Dev Tools), Pulse (Big Tech & Industry)',
    'active',
    '{"workspace_scope": "shared", "progress_notifications": true}'::jsonb,
    owner,
    tid,
    now(), now()
  );

  RAISE NOTICE 'Created team NewsCrew: %', team_uuid;

  -- ============================================================
  -- 4. ADD TEAM MEMBERS
  -- ============================================================
  INSERT INTO agent_team_members (team_id, agent_id, role, joined_at, tenant_id) VALUES
    (team_uuid, editor_id, 'lead', now(), tid),
    (team_uuid, globe_id, 'member', now(), tid),
    (team_uuid, nexus_id, 'member', now(), tid),
    (team_uuid, viet_id, 'member', now(), tid);

  RAISE NOTICE 'Added 4 members (1 lead + 3 members)';

  -- ============================================================
  -- 5. CREATE AGENT LINKS (Editor → each member)
  -- ============================================================
  INSERT INTO agent_links (id, source_agent_id, target_agent_id, direction, description, max_concurrent, settings, status, created_by, team_id, tenant_id, created_at, updated_at) VALUES
    (gen_random_uuid(), editor_id, globe_id, 'outbound', 'Editor delegates AI research news gathering to Globe', 1, '{}'::jsonb, 'active', owner, team_uuid, tid, now(), now()),
    (gen_random_uuid(), editor_id, nexus_id, 'outbound', 'Editor delegates developer tools news gathering to Nexus', 1, '{}'::jsonb, 'active', owner, team_uuid, tid, now(), now()),
    (gen_random_uuid(), editor_id, viet_id, 'outbound', 'Editor delegates Big Tech & industry news gathering to Pulse', 1, '{}'::jsonb, 'active', owner, team_uuid, tid, now(), now());

  RAISE NOTICE 'Created 3 agent links (Editor → Globe/Nexus/Pulse)';

  -- ============================================================
  -- 6. UPDATE CRON JOB: morning-briefing → NewsCrew Editor
  -- ============================================================
  UPDATE cron_jobs
  SET agent_id = editor_id,
      team_id = team_uuid,
      cron_expression = '0 2 * * *',
      timezone = 'UTC',
      payload = '{"kind": "agent_turn", "channel": "discord-bot", "deliver": true, "instruction": "Chạy bản tin sáng AI/Tech hôm nay. Dispatch 3 task song song cho Globe, Nexus, Pulse để thu thập tin. Sau đó tổng hợp thành bản tin hoàn chỉnh."}'::jsonb,
      updated_at = now()
  WHERE name = 'morning-briefing';

  RAISE NOTICE 'Updated cron job morning-briefing → NewsCrew Editor at 9:00 AM VN (UTC 02:00)';
  RAISE NOTICE 'NewsCrew setup complete!';
END $$;

-- ============================================================
-- VERIFY
-- ============================================================
SELECT '=== AGENTS ===' as section;
SELECT agent_key, display_name, status, provider, model FROM agents WHERE agent_key LIKE 'newscrew-%' ORDER BY agent_key;

SELECT '=== TEAM ===' as section;
SELECT t.name, t.status, (SELECT count(*) FROM agent_team_members WHERE team_id = t.id) as members
FROM agent_teams t WHERE t.name = 'NewsCrew';

SELECT '=== MEMBERS ===' as section;
SELECT a.agent_key, a.display_name, m.role FROM agent_team_members m
JOIN agents a ON a.id = m.agent_id
JOIN agent_teams t ON t.id = m.team_id
WHERE t.name = 'NewsCrew' ORDER BY m.role, a.agent_key;

SELECT '=== LINKS ===' as section;
SELECT sa.agent_key as source, ta.agent_key as target, l.direction
FROM agent_links l
JOIN agents sa ON sa.id = l.source_agent_id
JOIN agents ta ON ta.id = l.target_agent_id
WHERE sa.agent_key = 'newscrew-editor' ORDER BY ta.agent_key;

SELECT '=== CRON ===' as section;
SELECT cj.name, a.agent_key, cj.cron_expression, cj.timezone, t.name as team_name
FROM cron_jobs cj
JOIN agents a ON a.id = cj.agent_id
LEFT JOIN agent_teams t ON t.id = cj.team_id
WHERE cj.name = 'morning-briefing';
