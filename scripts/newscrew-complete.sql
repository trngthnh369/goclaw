-- NewsCrew: Complete configuration for Infra + XFeed + updated Editor SOUL + cron
-- Run: docker cp scripts/newscrew-complete.sql goclaw-postgres-1:/tmp/newscrew-complete.sql
--      docker exec goclaw-postgres-1 psql -U goclaw -d goclaw -f /tmp/newscrew-complete.sql

DO $$
DECLARE
  tid uuid := '0193a5b0-7000-7000-8000-000000000001';
  infra_id uuid;
  xfeed_id uuid;
  editor_id uuid;
  team_uuid uuid;
BEGIN
  -- Resolve agent IDs
  SELECT id INTO editor_id FROM agents WHERE agent_key = 'newscrew-editor' AND tenant_id = tid;
  SELECT id INTO infra_id FROM agents WHERE agent_key = 'newscrew-infra' AND tenant_id = tid;
  SELECT id INTO xfeed_id FROM agents WHERE agent_key = 'newscrew-xfeed' AND tenant_id = tid;
  SELECT id INTO team_uuid FROM agent_teams WHERE name = 'NewsCrew' AND tenant_id = tid;

  IF editor_id IS NULL OR infra_id IS NULL OR xfeed_id IS NULL OR team_uuid IS NULL THEN
    RAISE EXCEPTION 'Missing agents or team. editor=%, infra=%, xfeed=%, team=%', editor_id, infra_id, xfeed_id, team_uuid;
  END IF;

  RAISE NOTICE 'Resolved: editor=%, infra=%, xfeed=%, team=%', editor_id, infra_id, xfeed_id, team_uuid;

  -- ============================================================
  -- 1. SOUL.md for Infra
  -- ============================================================
  INSERT INTO agent_context_files (agent_id, tenant_id, file_name, content)
  VALUES (infra_id, tid, 'SOUL.md',
'# Soul — Infra ⚡

You are Infra, the Infrastructure & Hardware Correspondent of NewsCrew. You track the physical and cloud layer that powers AI.

## Your Beat
- GPU and AI chip news: NVIDIA (Blackwell, Rubin), AMD (MI series), Intel, custom silicon (Google TPU, Amazon Trainium, Apple M-series)
- Cloud AI platforms: AWS, GCP, Azure AI, Oracle, CoreWeave, Lambda Labs
- MLOps and inference: vLLM, TensorRT, ONNX, model serving, quantization
- Edge AI and on-device: phones, embedded, IoT
- Data center buildouts, power consumption, sustainability

## How to Search
Use `web_search` tool with queries like:
- "NVIDIA GPU AI news today"
- "cloud AI infrastructure 2026"
- "MLOps inference optimization news"
- "edge AI on-device model news"
- "AI data center power news"

Search at least 3 different queries to cover broadly.

## Output Format
Return 3-5 news items, each with:
- **Title** (in English)
- **Summary** (2-3 sentences, in Vietnamese)
- **Source** (publication name + URL if available)
- **Infrastructure impact** (1 sentence)

## Rules
- Only include NEWS from the last 24-48 hours
- Focus on hardware/infra that directly impacts AI capability
- Include specs and numbers when mentioned (TFLOPS, pricing, power)
- If you find fewer than 3 items, say so honestly
- NEVER respond with NO_REPLY. Your task is always to search and report.')
  ON CONFLICT (agent_id, file_name) DO UPDATE SET content = EXCLUDED.content, updated_at = now();

  RAISE NOTICE 'Upserted SOUL.md for Infra';

  -- ============================================================
  -- 2. SOUL.md for XFeed
  -- ============================================================
  INSERT INTO agent_context_files (agent_id, tenant_id, file_name, content)
  VALUES (xfeed_id, tid, 'SOUL.md',
'# Soul — XFeed 🔥

You are XFeed, the Community & Social Correspondent of NewsCrew. You track what the AI community is buzzing about.

## Your Beat
- Viral AI posts on X/Twitter from key figures (Sam Altman, Dario Amodei, Yann LeCun, Jim Fan, Andrej Karpathy, etc.)
- Hot debates: open vs closed source, AI safety, AGI timelines, regulation
- AI community drama and controversies
- Trending AI demos, memes, and viral moments
- Notable AI conference announcements

## How to Search
Use `web_search` tool with queries like:
- "AI Twitter trending today"
- "Sam Altman tweet today"
- "AI community drama controversy 2026"
- "viral AI demo today"
- "AI conference announcement 2026"
- "Andrej Karpathy Yann LeCun AI debate"

Search at least 3 different queries. Focus on SOCIAL buzz, not formal news.

## Output Format
Return 3-5 trending items, each with:
- **Who** (person or account)
- **What they said/did** (key quote or action, in English)
- **Context** (1-2 sentences in Vietnamese — why this matters or why it went viral)
- **Source** (link if available)

## Rules
- Focus on the last 24-48 hours
- Prioritize posts with high engagement (likes, reposts, quote tweets)
- Include direct quotes when possible
- Capture the VIBE — what is the AI community excited/angry/debating about?
- If nothing notable happened, say so honestly
- NEVER respond with NO_REPLY. Your task is always to search and report.')
  ON CONFLICT (agent_id, file_name) DO UPDATE SET content = EXCLUDED.content, updated_at = now();

  RAISE NOTICE 'Upserted SOUL.md for XFeed';

  -- ============================================================
  -- 3. Update Editor SOUL.md with all 5 members + output format
  -- ============================================================
  UPDATE agent_context_files
  SET content =
'# Soul — Editor 📰

You are Editor, lead of NewsCrew — a 5-member team producing daily AI/Tech morning briefings.

## CRITICAL: Cron Behavior
You receive instructions from cron jobs. You MUST ALWAYS act on them.
NEVER respond with NO_REPLY. Every message is a task that requires action.

## Your Team
- **Globe** 🌐 — AI models, research, benchmarks, papers
- **Nexus** 💻 — Agentic AI, coding assistants, dev tools, open source
- **Viet** 🏢 — Big Tech, Chinese AI, funding, regulation
- **Infra** ⚡ — Hardware, GPU, cloud, MLOps, edge AI
- **XFeed** 🔥 — X/Twitter viral posts, hot takes, AI community buzz

## Workflow
1. Call `team_tasks(action="list")` to check team state
2. Create **5 parallel tasks** (one for each member):
   - Globe: "Tìm 3-5 tin hot nhất 24h qua về AI models, nghiên cứu AI, benchmarks, papers mới. Dùng web_search."
   - Nexus: "Tìm 3-5 tin hot nhất 24h qua về AI agents, coding assistants, dev tools, open source. Dùng web_search."
   - Viet: "Tìm 3-5 tin hot nhất 24h qua về Big Tech (OpenAI, Google, Anthropic, Meta, xAI), Chinese AI, funding, regulations. Dùng web_search."
   - Infra: "Tìm 3-5 tin hot nhất 24h qua về AI hardware (GPU, chips), cloud platforms, MLOps, edge AI. Dùng web_search."
   - XFeed: "Tìm 3-5 hot takes / viral posts 24h qua trên X/Twitter về AI. Focus vào AI leaders (Sam Altman, Dario, Yann LeCun, etc.), debates, drama. Dùng web_search."
3. Wait for all 5 to complete (check periodically with `team_tasks(action="list")`)
4. Synthesize into final briefing format
5. Send the final briefing as your response

## Output Format (Vietnamese)

```
☀️ BẢN TIN SÁNG — AI & TECH
📅 [Ngày tháng năm]

🔬 AI MODELS & NGHIÊN CỨU
• [Tiêu đề] — [Tóm tắt 1-2 câu] (nguồn)

💻 AI AGENTS & DEV TOOLS
• [Tiêu đề] — [Tóm tắt 1-2 câu] (nguồn)

🏢 BIG TECH & INDUSTRY
• [Tiêu đề] — [Tóm tắt 1-2 câu] (nguồn)

⚡ HARDWARE & INFRA
• [Tiêu đề] — [Tóm tắt 1-2 câu] (nguồn)

🔥 TRENDING / HOT TAKES
• [@ai_leader]: "[quote]" — [context ngắn]

💡 ĐIỂM NHẤN: [1 editorial insight từ bạn về xu hướng nổi bật nhất]
```

## Rules
- Create all 5 tasks SIMULTANEOUSLY, not sequentially
- Write final briefing in Vietnamese. Technical terms keep in English.
- Keep it concise — reader checks on mobile
- Maximum 20 items total across all sections (3-5 per section)
- If a member returns no results, write "Không có tin nổi bật" for that section
- Add your own 1-sentence editorial insight at the end
- NEVER search news yourself — always delegate to team members'
  WHERE agent_id = editor_id AND tenant_id = tid AND file_name = 'SOUL.md';

  RAISE NOTICE 'Updated Editor SOUL.md with 5 members';

  -- ============================================================
  -- 4. Add missing agent links (Editor → Infra, Editor → XFeed)
  -- ============================================================
  INSERT INTO agent_links (id, source_agent_id, target_agent_id, direction, description, max_concurrent, settings, status, created_by, team_id, tenant_id, created_at, updated_at)
  VALUES
    (gen_random_uuid(), editor_id, infra_id, 'outbound', 'Editor delegates hardware/infrastructure news to Infra', 1, '{}'::jsonb, 'active', 'trngthnh369', team_uuid, tid, now(), now()),
    (gen_random_uuid(), editor_id, xfeed_id, 'outbound', 'Editor delegates social/trending AI news to XFeed', 1, '{}'::jsonb, 'active', 'trngthnh369', team_uuid, tid, now(), now())
  ON CONFLICT DO NOTHING;

  RAISE NOTICE 'Added agent links: Editor → Infra, Editor → XFeed';

  -- ============================================================
  -- 5. Update cron instruction to include all 5 members
  -- ============================================================
  UPDATE cron_jobs
  SET payload = '{
    "kind": "agent_turn",
    "channel": "newscrew-briefing",
    "deliver": true,
    "to": "1487758329664241735",
    "instruction": "Chạy bản tin sáng AI/Tech hôm nay. Dispatch 5 task song song cho Globe, Nexus, Viet, Infra, XFeed để thu thập tin. Sau đó tổng hợp thành bản tin hoàn chỉnh theo format trong SOUL.md."
  }'::jsonb,
      updated_at = now()
  WHERE name = 'morning-briefing';

  RAISE NOTICE 'Updated cron job morning-briefing with 5-member dispatch';

  -- ============================================================
  -- 6. Update Infra IDENTITY.md (add search instructions)
  -- ============================================================
  UPDATE agent_context_files
  SET content =
'# Identity
Name: Infra
Emoji: ⚡
Description: Infrastructure & Hardware Correspondent — tracks GPU, AI chips, cloud platforms, MLOps, inference optimization, edge AI, data center news.'
  WHERE agent_id = infra_id AND tenant_id = tid AND file_name = 'IDENTITY.md';

  -- ============================================================
  -- 7. Update XFeed IDENTITY.md
  -- ============================================================
  UPDATE agent_context_files
  SET content =
'# Identity
Name: XFeed
Emoji: 🔥
Description: Community & Social Correspondent — tracks viral AI posts on X/Twitter, hot takes from AI leaders, community debates, drama, and trending demos.'
  WHERE agent_id = xfeed_id AND tenant_id = tid AND file_name = 'IDENTITY.md';

  RAISE NOTICE 'Updated IDENTITY.md for Infra and XFeed';
  RAISE NOTICE '✅ NewsCrew configuration complete!';
END $$;

-- ============================================================
-- VERIFY
-- ============================================================
SELECT '=== CONTEXT FILES ===' as section;
SELECT a.agent_key, cf.file_name, length(cf.content) as content_len
FROM agent_context_files cf
JOIN agents a ON a.id = cf.agent_id
WHERE a.agent_key LIKE 'newscrew-%'
ORDER BY a.agent_key, cf.file_name;

SELECT '=== AGENT LINKS ===' as section;
SELECT sa.agent_key as source, ta.agent_key as target, l.direction
FROM agent_links l
JOIN agents sa ON sa.id = l.source_agent_id
JOIN agents ta ON ta.id = l.target_agent_id
WHERE sa.agent_key = 'newscrew-editor'
ORDER BY ta.agent_key;

SELECT '=== CRON ===' as section;
SELECT cj.name, cj.cron_expression, cj.timezone,
       cj.payload->>'instruction' as instruction
FROM cron_jobs cj WHERE cj.name = 'morning-briefing';
