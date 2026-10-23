---
name: morning-briefing
description: Daily morning news briefing. Use when asked for morning briefing, daily news summary, tin tức buổi sáng, or when triggered by cron schedule. Searches latest news on AI, tech, politics and delivers formatted summary via message tool.
license: Internal
metadata:
  author: trngthnh369
  version: "1.1.0"
  bundle_revision: "2026-09-21-001"
  runtime: python3
---

# Morning Briefing

Compile and deliver a daily news briefing covering key topics. Designed to run via cron (6AM UTC+7) or on-demand.

## Topics (search in order)

1. **AI & Agentic** — new agent frameworks, MCP updates, tool-use breakthroughs
2. **Big AI Companies** — OpenAI, Google DeepMind, Anthropic, Meta AI announcements
3. **Chinese AI** — DeepSeek, Qwen, ByteDance, Baidu models and regulations
4. **Tech** — major product launches, open source, developer tools
5. **Vietnam Politics** — policy changes, economic indicators, trade agreements
6. **International Politics** — geopolitics affecting tech/trade, US-China relations

## Workflow

1. **Search** — For each topic, use `web_search` with targeted queries:
   ```
   web_search("AI agent framework news today 2024")
   web_search("OpenAI Google Anthropic announcement this week")
   web_search("Chinese AI DeepSeek Qwen news")
   web_search("technology news today")
   web_search("Vietnam politics news today")
   web_search("international politics tech trade news")
   ```

2. **Filter** — Skip rumors, opinion pieces, duplicates. Prioritize:
   - Official announcements and launches
   - Verified research results
   - Policy/regulation changes
   - Events with direct impact on user's work (AI, ecommerce, automation)

3. **Format** — Write in Vietnamese, keep technical terms in English:
   ```
   🌅 BẢN TIN SÁNG — {date}

   🤖 AI & AGENTIC
   • [Headline] — [1-2 sentence summary]

   🏢 BIG AI
   • [Headline] — [1-2 sentence summary]

   🇨🇳 CHINESE AI
   • [Headline] — [1-2 sentence summary]

   💻 TECH
   • [Headline] — [1-2 sentence summary]

   🇻🇳 VIỆT NAM
   • [Headline] — [1-2 sentence summary]

   🌍 QUỐC TẾ
   • [Headline] — [1-2 sentence summary]

   ---
   Tổng hợp tự động bởi GoClaw Researcher
   ```

4. **Deliver** — Send via `message` tool to configured channel:
   ```
   message(channel="zalo-personal-bot", to="367617605136702044", content=formatted_briefing)
   ```

## Cross-day dedup — `scripts/dedup.py`

The cron run is stateless and the sources (HN front page, GitHub trending, section pages) keep the same
stories up for days, so without a ledger the briefing repeats itself: replaying 2026-09-01..21, 98 of
351 bullets (28%) had already run in the previous 7 days. `dedup.py` is that ledger.

Run it once, after picking stories and BEFORE writing the briefing or seeding cf-director:

```bash
SKILLDIR=$(ls -d /app/data/skills-store/morning-briefing/*/ | sort -V | tail -1)
python3 "$SKILLDIR/scripts/dedup.py" check --workspace /app/workspace/news-briefer <<'JSON'
[{"title":"<tiêu đề tiếng Việt sẽ viết>","url":"https://<link bài gốc>"}]
JSON
```

- `drop` = same canonical URL or same title within the window: never brief it.
- `similar` = shares 2+ distinctive tokens (names, product codes, figures) with a recent item: skip,
  unless today's source has a concrete new development, then prefix `[Cập nhật]` and say what changed.
- `keep` = new. SEED_TOPICS for cf-director come only from here.
- Recording happens inside `check`, so there is no second call to forget. The window (7 days) runs on
  `last_seen`: a story a source keeps offering stays blocked for as long as it is offered.
- Listing URLs (`/category/...`, homepages) are never used as a key; tokens that recur in 3+ ledger
  titles (`anthropic`, `gpt-6`, `hugging face`) are ignored for `similar`.
- Ledger: `/app/workspace/news-briefer/memory/briefed.ndjson`, 30-day retention.
- Seed or backfill from past briefs: `dedup.py import --date YYYY-MM-DD` with the briefing text on stdin.

Tests: `python3 -m unittest discover -s <skill dir>/tests`.

⚠️ The live agent reads its workflow from `SOUL.md` and the cron payload, not from this file
(`use_skill` does not inject SKILL.md). Changing the command here means changing it there too.

## On-Demand Usage

User can ask: "cho tôi bản tin sáng" or "morning briefing" at any time.
When on-demand, skip the message delivery step — just return the formatted briefing in chat.

## Cron Setup

```json
{
  "name": "morning-briefing",
  "schedule": {"kind": "cron", "expr": "0 23 * * *", "tz": "UTC"},
  "message": "Run morning briefing skill",
  "deliver": true,
  "channel": "zalo-personal-bot",
  "to": "367617605136702044"
}
```

## Quality Rules

- Maximum 3 items per topic (skip if nothing newsworthy)
- Each item ≤ 2 sentences
- Total briefing ≤ 2000 characters (Zalo message limit)
- If a topic has no news today, write "Không có tin nổi bật" and move on
- Always include the date in header
