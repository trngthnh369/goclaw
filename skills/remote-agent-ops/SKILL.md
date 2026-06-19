---
name: remote-agent-ops
description: Use this skill when the user wants to connect to, manage, operate, monitor, troubleshoot, or configure a REMOTE GoClaw server. This includes triggering remote agents, reading remote messages, checking remote health, running remote diagnostics, or any admin task on a GoClaw instance that is NOT the local one. If the user mentions "remote", "server", "company server", "production", or gives an external URL/IP for GoClaw operations, use this skill.
metadata:
  author: Commander
  version: "1.0.0"
---

# Remote GoClaw Server Operations

## Connection Setup

On first interaction with a remote server, establish and save connection info:

```bash
# Required: remote URL and gateway token
REMOTE_URL="https://company-goclaw.example.com:18790"
REMOTE_TOKEN="the-gateway-token"

# Verify connection
curl -s -H "Authorization: Bearer $REMOTE_TOKEN" $REMOTE_URL/health
```

Save the URL to memory (NOT the token). Ask user to set token as env var if not already available.

## Core API Pattern

```bash
# GET (read)
curl -s -H "Authorization: Bearer $REMOTE_TOKEN" $REMOTE_URL/v1/{endpoint}

# POST (create/action)
curl -s -X POST -H "Authorization: Bearer $REMOTE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"key":"value"}' $REMOTE_URL/v1/{endpoint}

# PUT (update)
curl -s -X PUT -H "Authorization: Bearer $REMOTE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"key":"value"}' $REMOTE_URL/v1/{endpoint}

# DELETE (remove — always confirm first)
curl -s -X DELETE -H "Authorization: Bearer $REMOTE_TOKEN" $REMOTE_URL/v1/{endpoint}
```

## Trigger Remote Agent (chat/completions)

Send a message to any agent on the remote server and get a response:

```bash
curl -s -X POST -H "Authorization: Bearer $REMOTE_TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-User-ID: admin" \
  -d '{
    "model": "{agent_key_or_uuid}",
    "messages": [{"role": "user", "content": "Your instruction here"}],
    "stream": false
  }' $REMOTE_URL/v1/chat/completions
```

Parameters:
- `model`: agent_key or agent UUID (identifies which agent to trigger)
- `messages`: array of messages (last user message is the instruction)
- `stream`: false for full response, true for SSE streaming
- `user`: optional user ID for per-user session scoping
- Header `X-User-ID`: identifies the calling user for session routing

Use this to:
- Ask a remote agent to perform a task
- Test agent behavior after configuration changes
- Run diagnostic queries through a remote agent

## Wake/Trigger Agent

Simpler alternative to chat/completions for one-shot triggers:

```bash
curl -s -X POST -H "Authorization: Bearer $REMOTE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Your instruction here",
    "user_id": "admin",
    "session_key": "optional-session-key"
  }' $REMOTE_URL/v1/agents/{agent_id_or_key}/wake
```

Returns: `{"content": "agent response", "run_id": "...", "usage": {...}}`

## Direct Tool Invocation

Call any tool on the remote server directly:

```bash
curl -s -X POST -H "Authorization: Bearer $REMOTE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "tool": "tool_name",
    "action": "optional_action",
    "args": {"param1": "value1"},
    "agentId": "agent-uuid",
    "dryRun": false
  }' $REMOTE_URL/v1/tools/invoke
```

Use cases:
- `tool=cron, action=list` — list all cron jobs
- `tool=cron, action=add` — create cron job
- `tool=sessions_list` — list active sessions
- `tool=memory_search, args={"query":"..."}` — search agent memory

## Read Channel Group Messages

```bash
# List all message groups across channels
curl -s -H "Authorization: Bearer $REMOTE_TOKEN" $REMOTE_URL/v1/pending-messages

# Read messages from specific group
curl -s -H "Authorization: Bearer $REMOTE_TOKEN" \
  "$REMOTE_URL/v1/pending-messages/messages?channel=zalo&key=group:123456"

# Compact old messages (LLM summarization)
curl -s -X POST -H "Authorization: Bearer $REMOTE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"channel":"zalo","key":"group:123456"}' \
  $REMOTE_URL/v1/pending-messages/compact
```

## Health & Diagnostics

```bash
# Basic health
curl -s -H "Authorization: Bearer $REMOTE_TOKEN" $REMOTE_URL/health

# Usage stats (period: 1h, 24h, 7d, 30d)
curl -s -H "Authorization: Bearer $REMOTE_TOKEN" "$REMOTE_URL/v1/usage/summary?period=24h"

# Usage breakdown by agent
curl -s -H "Authorization: Bearer $REMOTE_TOKEN" "$REMOTE_URL/v1/usage/breakdown?period=24h"

# Time series data
curl -s -H "Authorization: Bearer $REMOTE_TOKEN" "$REMOTE_URL/v1/usage/timeseries?period=7d"

# Recent activity
curl -s -H "Authorization: Bearer $REMOTE_TOKEN" "$REMOTE_URL/v1/activity?limit=50"

# Cost summary
curl -s -H "Authorization: Bearer $REMOTE_TOKEN" "$REMOTE_URL/v1/costs/summary?period=7d"

# Traces (LLM call details)
curl -s -H "Authorization: Bearer $REMOTE_TOKEN" "$REMOTE_URL/v1/traces?limit=20"

# Single trace detail
curl -s -H "Authorization: Bearer $REMOTE_TOKEN" "$REMOTE_URL/v1/traces/{traceID}"
```

## System Inventory (Full Audit)

Run these in sequence to get complete picture of remote server state:

```bash
# 1. Agents
curl -s -H "Authorization: Bearer $REMOTE_TOKEN" $REMOTE_URL/v1/agents | jq '.[] | {agent_key, display_name, status, provider, model}'

# 2. Providers
curl -s -H "Authorization: Bearer $REMOTE_TOKEN" $REMOTE_URL/v1/providers | jq '.[] | {name, provider_type, enabled}'

# 3. Channels
curl -s -H "Authorization: Bearer $REMOTE_TOKEN" $REMOTE_URL/v1/channel-instances | jq '.[] | {name, channel_type, enabled}'

# 4. MCP servers
curl -s -H "Authorization: Bearer $REMOTE_TOKEN" $REMOTE_URL/v1/mcp/servers | jq '.[] | {name, transport, enabled}'

# 5. Skills
curl -s -H "Authorization: Bearer $REMOTE_TOKEN" $REMOTE_URL/v1/skills | jq '.[] | {name, description}'

# 6. Teams
curl -s -H "Authorization: Bearer $REMOTE_TOKEN" $REMOTE_URL/v1/teams

# 7. API Keys
curl -s -H "Authorization: Bearer $REMOTE_TOKEN" $REMOTE_URL/v1/api-keys | jq '.[] | {name, scopes, created_at}'
```

## Troubleshooting

### Agent not responding
1. Check health: `GET /health`
2. Check provider: `GET /v1/providers` → verify enabled + API key valid
3. Test provider: `POST /v1/providers/{id}/verify`
4. Check agent status: `GET /v1/agents/{key}` → status should be "active"
5. Test with wake: `POST /v1/agents/{id}/wake` with simple message

### Channel disconnected
1. List channels: `GET /v1/channel-instances`
2. Check enabled status and error messages
3. Re-create if needed with same credentials

### High cost / token usage
1. Check usage: `GET /v1/usage/breakdown?period=7d`
2. Identify top-consuming agents
3. Reduce context_window, thinking_level, or switch to cheaper model
4. Check for runaway cron jobs: invoke `cron list` via tools/invoke

### Session issues
1. List sessions: `GET /v1/sessions?agent_id={id}`
2. Delete stuck session: `DELETE /v1/sessions/{key}`
3. Check pending messages: `GET /v1/pending-messages`

## Tenant Management (Multi-tenant)

```bash
# List tenants
curl -s -H "Authorization: Bearer $REMOTE_TOKEN" $REMOTE_URL/v1/tenants

# Create tenant
curl -s -X POST -H "Authorization: Bearer $REMOTE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"New Tenant","slug":"new-tenant"}' \
  $REMOTE_URL/v1/tenants

# List tenant users
curl -s -H "Authorization: Bearer $REMOTE_TOKEN" $REMOTE_URL/v1/tenants/{id}/users

# Add user to tenant
curl -s -X POST -H "Authorization: Bearer $REMOTE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"username","role":"admin"}' \
  $REMOTE_URL/v1/tenants/{id}/users
```

## Safety Rules for Remote Operations

- ALWAYS verify connection with `/health` before starting
- ALWAYS GET current state before making changes
- NEVER store remote tokens in memory — use env vars
- NEVER delete resources without explicit user confirmation
- ALWAYS report what changed after each write operation
- If unsure about remote server state, do a full inventory first
