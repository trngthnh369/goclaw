---
name: polymarket-intel
description: "Use this agent configuration guide to set up the Polymarket Intel agent and cron job on GoClaw."
---

# Polymarket Intel — Agent & Cron Setup Guide

## Prerequisites

1. GoClaw server running with Discord adapter connected
2. Discord channel `#polymarket-alerts` created (note the channel ID)
3. An API key or gateway token for admin operations

## Step 1: Create the Agent via API

```bash
# Replace $URL and $TOKEN with your GoClaw gateway URL and auth token

curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Polymarket Intel",
    "slug": "polymarket-intel",
    "type": "predefined",
    "description": "Monitors Polymarket prediction markets for unusual trading activity (volume spikes, large bets, price swings) that may signal insider knowledge. Runs hourly via cron.",
    "model": "gpt-4o",
    "provider_id": "<YOUR_OPENAI_PROVIDER_ID>",
    "tool_policy": "minimal",
    "tool_also_allow": ["exec"],
    "skill_allow_list": ["polymarket-scanner"],
    "self_evolve": false,
    "skill_evolve": false,
    "memory_config": {
      "enabled": false
    }
  }' "$URL/v1/agents"
```

### Key Design Decisions:

| Setting | Value | Why |
|---------|-------|-----|
| `type` | `predefined` | Shared config, consistent behavior across runs |
| `tool_policy` | `minimal` | Restrict tools to minimum needed |
| `tool_also_allow` | `["exec"]` | Only exec to run Python script |
| **No** `web_fetch`, `web_search` | — | **CRITICAL: prevents LLM from calling APIs directly** |
| `self_evolve` | `false` | No need — fixed purpose agent |
| `memory_config.enabled` | `false` | Each cron run is independent — no memory needed |

## Step 2: Set Agent Identity (IDENTITY.md)

Create file at `agents/polymarket-intel/IDENTITY.md`:

```markdown
You are **Polymarket Intel**, a market intelligence analyst specializing in prediction market anomaly detection.

## Your Role
- You monitor Polymarket for unusual trading patterns that may signal insider knowledge
- You analyze structured data from the polymarket_scanner Python skill
- You compose clear, actionable Discord alerts

## Rules
1. **NEVER fabricate market data.** All numbers come from the Python script.
2. You add VALUE by providing geopolitical/economic context for why an anomaly matters.
3. If the script shows errors, report them honestly. Do NOT guess.
4. Keep Discord messages concise but informative.
5. When no anomalies are found, respond with just the heartbeat message (no Discord).
```

## Step 3: Create Cron Job via API

```bash
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "polymarket-hourly-scan",
    "agent_id": "<AGENT_ID_FROM_STEP_1>",
    "schedule": "every 1 hour",
    "message": "Run the polymarket-scanner skill now. Execute: python3 scripts/scanner.py --mode=full\n\nRead the JSON output carefully. If anomalies are found (total_anomalies > 0), compose a Discord alert following the format in SKILL.md. If NO anomalies, respond only: No anomalies detected across {markets_scanned} markets. Next scan in 1 hour.",
    "deliver": "channel",
    "channel": "discord",
    "chat_id": "<DISCORD_CHANNEL_ID>",
    "enabled": true,
    "isolated_session": true,
    "light_context": true
  }' "$URL/v1/cron"
```

### Cron Settings Explained:

| Setting | Value | Why |
|---------|-------|-----|
| `schedule` | `every 1 hour` | Hourly monitoring |
| `isolated_session` | `true` | Fresh session each run — no history contamination |
| `light_context` | `true` | Minimal system prompt — faster, cheaper |
| `deliver: channel` | `discord` | Send to Discord when anomalies found |

## Step 4: Set Up Market Discovery Cron

The watchlist needs daily refresh to catch new markets:

```bash
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "polymarket-daily-discovery",
    "agent_id": "<AGENT_ID>",
    "schedule": "at 06:00",
    "message": "Refresh the Polymarket watchlist. Execute: python3 scripts/scanner.py --mode=discover\n\nReport how many markets were found per category.",
    "enabled": true,
    "isolated_session": true,
    "light_context": true
  }' "$URL/v1/cron"
```

## Step 5: Test

```bash
# 1. Test discovery
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "Run: python3 scripts/scanner.py --mode=discover"}' \
  "$URL/v1/agents/<AGENT_ID>/chat"

# 2. Test full scan
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "Run: python3 scripts/scanner.py --mode=full"}' \
  "$URL/v1/agents/<AGENT_ID>/chat"

# 3. Test specific market
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "Run: python3 scripts/scanner.py --mode=check --market=iran"}' \
  "$URL/v1/agents/<AGENT_ID>/chat"
```

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `requests` not available | Script uses `urllib` (stdlib) — no dependencies needed |
| API rate limited | Script has exponential backoff built in |
| Empty watchlist | Run `--mode=discover` first |
| No anomalies ever | Check thresholds in scanner.py — lower if needed |
| Discord not receiving | Verify `chat_id` is correct Discord channel ID |
