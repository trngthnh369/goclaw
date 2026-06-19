---
name: system-diagnostics
description: Use this skill when the user wants to check system health, diagnose problems, audit security, analyze performance, optimize costs, or troubleshoot any GoClaw issue. Trigger words include "health check", "diagnose", "audit", "troubleshoot", "why is X not working", "performance", "cost optimization", "security check", "system status", or any question about why something is broken or slow.
metadata:
  author: Operator
  version: "1.0.0"
---

# System Diagnostics — Systematic Troubleshooting & Auditing

## Quick Health Check (< 1 minute)

Run these 3 checks in sequence. Stop if any fails.

```bash
# 1. Server alive?
curl -s -H "Authorization: Bearer $TOKEN" $URL/health

# 2. Providers working?
curl -s -H "Authorization: Bearer $TOKEN" $URL/v1/providers | jq '.[] | {name, enabled, provider_type}'

# 3. Recent errors?
curl -s -H "Authorization: Bearer $TOKEN" "$URL/v1/activity?limit=20" | jq '.[] | select(.status == "error") | {agent, error, created_at}'
```

If all pass → system is healthy. If any fails → proceed to relevant deep diagnostic below.

---

## Full System Audit (5-10 minutes)

Use when: first time connecting to a server, after incidents, or user asks "what's the status?"

### Step 1: Inventory

```bash
# Count all resources
AGENTS=$(curl -s -H "Authorization: Bearer $TOKEN" $URL/v1/agents)
PROVIDERS=$(curl -s -H "Authorization: Bearer $TOKEN" $URL/v1/providers)
CHANNELS=$(curl -s -H "Authorization: Bearer $TOKEN" $URL/v1/channel-instances)
MCP=$(curl -s -H "Authorization: Bearer $TOKEN" $URL/v1/mcp/servers)
SKILLS=$(curl -s -H "Authorization: Bearer $TOKEN" $URL/v1/skills)
TEAMS=$(curl -s -H "Authorization: Bearer $TOKEN" $URL/v1/teams)
KEYS=$(curl -s -H "Authorization: Bearer $TOKEN" $URL/v1/api-keys)
```

Report as table:
| Resource | Count | Active | Issues |
|----------|-------|--------|--------|
| Agents | N | N active | ... |
| Providers | N | N enabled | ... |
| Channels | N | N enabled | ... |
| MCP Servers | N | N enabled | ... |
| Skills | N | - | ... |
| Teams | N | - | ... |
| API Keys | N | - | ... |

### Step 2: Provider Health

For each provider:
```bash
# Verify connection
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  $URL/v1/providers/{id}/verify
```

Flag: disabled providers, failed verify, missing API keys.

### Step 3: Usage & Costs (last 24h)

```bash
curl -s -H "Authorization: Bearer $TOKEN" "$URL/v1/usage/summary?period=24h"
curl -s -H "Authorization: Bearer $TOKEN" "$URL/v1/usage/breakdown?period=24h"
curl -s -H "Authorization: Bearer $TOKEN" "$URL/v1/costs/summary?period=24h"
```

Flag: unusually high token usage, costly agents, inactive agents consuming resources.

### Step 4: Channel Connectivity

```bash
curl -s -H "Authorization: Bearer $TOKEN" $URL/v1/channel-instances | \
  jq '.[] | {name, channel_type, enabled, agent_id}'
```

Flag: disabled channels, channels pointing to inactive agents.

### Step 5: Pending Messages Backlog

```bash
curl -s -H "Authorization: Bearer $TOKEN" $URL/v1/pending-messages
```

Flag: large backlogs (>100 unread messages in any group).

---

## Troubleshooting: Agent Not Responding

**Symptoms:** User says agent doesn't reply, wake returns error, chat/completions hangs.

```
1. Check agent exists and is active:
   GET /v1/agents/{key_or_id}
   → status must be "active"
   → provider and model fields must be set

2. Check provider is working:
   GET /v1/providers → find the agent's provider
   POST /v1/providers/{id}/verify
   → If verify fails: API key invalid or provider down

3. Check for stuck sessions:
   GET /v1/sessions?agent_id={id}
   → If a session shows "running", it may be stuck
   → DELETE /v1/sessions/{key} to clear (confirm with user first)

4. Test with minimal message:
   POST /v1/agents/{id}/wake
   Body: {"message": "Say hello", "user_id": "test"}
   → If timeout: provider is slow or down
   → If error: check error message against ERROR_PATTERNS.md

5. Check recent traces for errors:
   GET /v1/traces?limit=10&agent_id={id}
   → Look for error status, high latency, context overflow
```

---

## Troubleshooting: Channel Disconnected

**Symptoms:** Messages in channel not reaching agent, bot appears offline.

```
1. List channels:
   GET /v1/channel-instances
   → Check enabled=true for the channel

2. Verify agent is active:
   GET /v1/agents/{agent_id_from_channel}

3. Check credentials:
   → Telegram: bot_token must be valid (GET https://api.telegram.org/bot{token}/getMe)
   → Discord: bot_token must be valid, bot must be in server
   → Zalo: OA credentials may have expired (refresh tokens)

4. Re-create channel if needed:
   DELETE /v1/channel-instances/{id}  (confirm first!)
   POST /v1/channel-instances with same credentials

5. Test by sending a message in the channel
```

---

## Troubleshooting: High Cost / Token Usage

**Symptoms:** Unexpected bills, high token consumption.

```
1. Get usage breakdown:
   GET /v1/usage/breakdown?period=7d
   → Identify top-consuming agents

2. For each high-usage agent, check:
   a. context_window — is it unnecessarily large?
   b. thinking_level — "high" costs 2-3x more tokens
   c. max_tool_iterations — high values = more LLM calls per request
   d. compaction_config — low minMessages = frequent re-summarization

3. Check for runaway cron jobs:
   POST /v1/tools/invoke
   Body: {"tool": "cron", "action": "list", "agentId": "{id}"}
   → Cron jobs with short intervals (< 5 min) can rack up costs

4. Check for chatty channels:
   GET /v1/pending-messages
   → High-volume groups trigger frequent agent runs

5. Optimization actions:
   - Switch to cheaper model (gemini-2.5-flash vs gemini-2.5-pro)
   - Set thinking_level: "none" for simple chat agents
   - Increase compaction_config.minMessages
   - Reduce max_tool_iterations
   - Disable unnecessary cron jobs
   - Set tools_config.deny to remove unused tools (reduces prompt size)
```

---

## Troubleshooting: Memory / Knowledge Not Working

**Symptoms:** Agent can't find uploaded documents, search returns empty.

```
1. Check memory is enabled:
   GET /v1/agents/{id}
   → memory_config.enabled must be true

2. List documents:
   GET /v1/agents/{id}/memory/documents
   → Verify documents were uploaded

3. Check indexing:
   GET /v1/agents/{id}/memory/chunks
   → If empty: documents uploaded but NOT indexed
   → Fix: POST /v1/agents/{id}/memory/index-all

4. Test search:
   POST /v1/agents/{id}/memory/search
   Body: {"query": "test query matching document content"}
   → If empty after indexing: embedding provider may be missing/broken

5. Check embedding provider:
   → Memory indexing requires a working embedding model
   → Usually the agent's provider must support embeddings
```

---

## Security Audit

Run this when: setting up new server, after security incident, periodic review.

```
1. API Keys audit:
   GET /v1/api-keys
   → Check scopes: no key should have operator.admin unless necessary
   → Check for unused/old keys
   → Verify no keys have overly broad scopes

2. Agent tool configs:
   GET /v1/agents → for each agent:
   → Check tools_config.deny — are dangerous tools blocked?
   → Check shell_deny_groups — are rm, credential_theft, etc. enabled?
   → Check subagents_config — reasonable limits?

3. Provider API keys:
   GET /v1/providers
   → Verify all providers have API keys set (keys are masked in response)
   → Disable unused providers

4. MCP server permissions:
   GET /v1/mcp/servers → for each:
   GET /v1/mcp/servers/{id}/grants/agents
   → Check which agents have access to which MCP servers
   → Verify no agent has unnecessary MCP access

5. Channel credentials:
   GET /v1/channel-instances
   → Verify only necessary channels are enabled
   → Check for test/dev channels left enabled in production

6. Shell deny groups:
   GET /v1/shell-deny-groups
   → Review available deny groups
   → Ensure production agents have appropriate restrictions

7. Owner visibility:
   → GOCLAW_OWNER_IDS should be set to limit who sees all agents
```

---

## Performance Analysis

```
1. Response time analysis:
   GET /v1/traces?limit=50
   → Calculate average response time
   → Identify slow traces (> 30s)
   → Check if slowness is provider latency vs tool execution

2. Context usage:
   GET /v1/traces?limit=20
   → Check token counts vs context_window
   → Agents frequently hitting >75% trigger compaction (expensive)
   → Solution: increase context_window or reduce conversation depth

3. Tool iteration analysis:
   GET /v1/traces?limit=20
   → Check tool_calls count per trace
   → Agents hitting max_tool_iterations are being truncated
   → Solution: increase limit or improve agent instructions

4. Compaction frequency:
   Look for traces with high input tokens followed by much lower
   → Frequent compaction = wasted tokens
   → Increase compaction_config.minMessages
```

---

## Report Template

After any diagnostic, report findings in this format:

```
## System Diagnostic Report — {date}

### Summary
- Overall Status: ✅ Healthy / ⚠️ Issues Found / ❌ Critical
- Server: {url}
- Checked: {timestamp}

### Findings
| # | Severity | Area | Issue | Recommendation |
|---|----------|------|-------|----------------|
| 1 | 🔴/🟡/🟢 | ... | ... | ... |

### Actions Taken
- (list any fixes applied during diagnostic)

### Recommended Follow-up
- (list items that need user decision or manual action)
```

Save this report to memory for tracking over time.
