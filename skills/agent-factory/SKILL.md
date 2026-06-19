---
name: agent-factory
description: Use this skill when the user wants to create a new agent, design an agent configuration, or set up an agent for a specific purpose. This includes creating chatbots, monitors, coders, translators, customer support agents, content writers, or any specialized AI agent. If the user says "create agent", "new agent", "set up agent", "agent for X", or describes what they want an agent to do, use this skill.
metadata:
  author: Commander
  version: "1.0.0"
---

# Agent Factory — Create Agents by Purpose

## How to Create an Agent

```bash
# 1. Create agent
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{...agent config...}' $URL/v1/agents

# 2. Set context files (predefined agents)
curl -s -X PUT -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: text/plain" \
  --data-binary 'File content here' \
  "$URL/v1/agents/{id}/instances/{ownerID}/files/USER.md"

# 3. Connect to channel (optional)
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{...channel config...}' $URL/v1/channel-instances

# 4. Grant MCP/skills (optional)
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"agent_id":"<uuid>"}' $URL/v1/mcp/servers/{id}/grants/agent

# 5. Verify
curl -s -H "Authorization: Bearer $TOKEN" $URL/v1/agents/{id}
```

## Agent Templates

### General Chatbot
For casual conversation, Q&A, general assistance.

```json
{
  "agent_key": "chatbot",
  "display_name": "Chatbot",
  "owner_id": "{owner}",
  "provider": "gemini",
  "model": "gemini-2.5-flash-preview-04-17",
  "agent_type": "open",
  "context_window": 65000,
  "max_tool_iterations": 10,
  "tools_config": {
    "deny": ["exec", "browser", "cron", "spawn", "create_image", "create_video", "create_audio"]
  },
  "memory_config": {"enabled": false},
  "compaction_config": {"minMessages": 50},
  "other_config": {
    "emoji": "💬",
    "description": "Friendly chatbot for general conversation",
    "thinking_level": "none"
  }
}
```
Best for: Low cost, fast responses, simple Q&A.

### Customer Support Agent
For handling customer inquiries with knowledge base access.

```json
{
  "agent_key": "support-agent",
  "display_name": "Support Agent",
  "owner_id": "{owner}",
  "provider": "gemini",
  "model": "gemini-2.5-flash-preview-04-17",
  "agent_type": "predefined",
  "context_window": 128000,
  "max_tool_iterations": 15,
  "tools_config": {
    "deny": ["exec", "browser", "cron", "spawn", "create_image", "create_video", "create_audio"]
  },
  "memory_config": {"enabled": true},
  "compaction_config": {"minMessages": 100},
  "other_config": {
    "emoji": "🎧",
    "description": "Customer support agent with knowledge base",
    "thinking_level": "medium"
  }
}
```
After creation: Upload product knowledge via Memory/Documents API, then index.
Best for: Customer service channels (Zalo, Telegram, WhatsApp).

### Coding Assistant
For code generation, review, debugging.

```json
{
  "agent_key": "coder",
  "display_name": "Coder",
  "owner_id": "{owner}",
  "provider": "anthropic",
  "model": "claude-sonnet-4-5-20250514",
  "agent_type": "predefined",
  "context_window": 200000,
  "max_tool_iterations": 30,
  "tools_config": {
    "deny": ["create_image", "create_video", "create_audio", "tts", "browser"]
  },
  "memory_config": {"enabled": true},
  "compaction_config": {"minMessages": 200},
  "subagents_config": {"maxSpawnDepth": 1, "maxConcurrent": 3, "maxChildrenPerAgent": 3},
  "other_config": {
    "emoji": "👨‍💻",
    "description": "Software development assistant",
    "thinking_level": "high"
  }
}
```
Best for: Dev teams, code review, debugging. Grant GitHub MCP for repo access.

### Translator / Content Writer
For translation, content creation, copywriting.

```json
{
  "agent_key": "writer",
  "display_name": "Writer",
  "owner_id": "{owner}",
  "provider": "gemini",
  "model": "gemini-2.5-pro-preview-05-06",
  "agent_type": "predefined",
  "context_window": 128000,
  "max_tool_iterations": 15,
  "tools_config": {
    "deny": ["exec", "browser", "cron", "spawn", "create_image", "create_video", "create_audio"]
  },
  "memory_config": {"enabled": true},
  "compaction_config": {"minMessages": 100},
  "other_config": {
    "emoji": "✍️",
    "description": "Content writer and translator",
    "thinking_level": "medium"
  }
}
```
Best for: Marketing, translation, blog posts, documentation.

### Channel Monitor
For monitoring group messages and generating summaries/reports.

```json
{
  "agent_key": "monitor-{channel}",
  "display_name": "Monitor ({channel})",
  "owner_id": "{owner}",
  "provider": "gemini",
  "model": "gemini-2.5-flash-preview-04-17",
  "agent_type": "predefined",
  "context_window": 128000,
  "max_tool_iterations": 20,
  "tools_config": {
    "deny": ["exec", "browser", "spawn", "create_image", "create_video", "create_audio"]
  },
  "memory_config": {"enabled": true},
  "compaction_config": {"minMessages": 200},
  "other_config": {
    "emoji": "📊",
    "description": "Monitors channel messages and generates periodic summaries",
    "thinking_level": "medium"
  }
}
```
After creation:
1. Connect to channel group
2. Set up cron job for periodic summaries
3. Configure message tool for sending reports to target chat
Best for: Zalo group monitoring, Telegram group summaries, daily digests.

### Research / Data Analyst
For web research, data analysis, report generation.

```json
{
  "agent_key": "researcher",
  "display_name": "Researcher",
  "owner_id": "{owner}",
  "provider": "gemini",
  "model": "gemini-2.5-pro-preview-05-06",
  "agent_type": "predefined",
  "context_window": 200000,
  "max_tool_iterations": 30,
  "tools_config": {
    "deny": ["create_image", "create_video", "create_audio", "tts"]
  },
  "memory_config": {"enabled": true},
  "compaction_config": {"minMessages": 150},
  "subagents_config": {"maxSpawnDepth": 2, "maxConcurrent": 4, "maxChildrenPerAgent": 5},
  "other_config": {
    "emoji": "🔬",
    "description": "Research and analysis agent with web access and subagents",
    "thinking_level": "high"
  }
}
```
Best for: Market research, competitive analysis, data gathering.

## Configuration Guide

### Model Selection
| Use Case | Recommended | Why |
|----------|-------------|-----|
| Simple chat | gemini-2.5-flash | Cheapest, fast |
| Complex reasoning | gemini-2.5-pro | Best quality/cost ratio |
| Coding | claude-sonnet-4-5 | Best at code |
| Creative writing | gemini-2.5-pro | Good creative output |
| Budget-conscious | gemini-2.5-flash | Lowest cost |

### Context Window Guide
| Scenario | Recommended |
|----------|-------------|
| Short conversations | 32K-65K |
| Normal use | 128K |
| Long sessions / admin | 200K |

### Thinking Level
| Level | Use Case | Cost Impact |
|-------|----------|-------------|
| none | Simple chat, FAQ | Lowest |
| medium | General tasks | Moderate |
| high | Complex reasoning, admin | Highest |

### Tools to Deny by Role
| Agent Role | Deny These |
|------------|-----------|
| Chatbot | exec, browser, cron, spawn, media creation |
| Support | exec, browser, cron, spawn, media creation |
| Coder | media creation, tts |
| Writer | exec, browser, cron, spawn, media creation |
| Monitor | exec, browser, spawn, media creation |
| Admin | media creation only |

## Post-Creation Checklist

1. Verify agent is active: `GET /v1/agents/{id}`
2. Set context files if predefined (IDENTITY.md, SOUL.md via Dashboard or SQL)
3. Upload knowledge if needed (Memory/Documents API)
4. Connect channel if needed
5. Grant MCP servers if needed
6. Grant skills if needed
7. Test with a message: `POST /v1/agents/{id}/wake`
8. Set up cron if monitoring agent
