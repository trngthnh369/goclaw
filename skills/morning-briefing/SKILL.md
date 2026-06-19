---
name: morning-briefing
description: Daily morning news briefing. Use when asked for morning briefing, daily news summary, tin tức buổi sáng, or when triggered by cron schedule. Searches latest news on AI, tech, politics and delivers formatted summary via message tool.
license: Internal
metadata:
  author: trngthnh369
  version: "1.0.0"
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
