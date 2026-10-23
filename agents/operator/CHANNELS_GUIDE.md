# CHANNELS_GUIDE.md — Channel Configuration Reference

> Complete reference for creating and configuring channel instances.
> Use this when setting up channels for agents.

---

## Channel Instance API

### Create Channel Instance
```bash
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "unique-name",
    "display_name": "Human Readable Name",
    "channel_type": "telegram|discord|zalo_personal|zalo_oa|whatsapp|slack|feishu",
    "agent_id": "agent-uuid",
    "credentials": { ... },
    "config": { ... },
    "enabled": true
  }' $URL/v1/channel-instances
```

### Update Channel Instance
```bash
curl -s -X PUT -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{ "config": { ... } }' \
  $URL/v1/channel-instances/{id}
```

### List / Get / Delete
```bash
GET  /v1/channel-instances              # list all
GET  /v1/channel-instances/{id}         # get one
DELETE /v1/channel-instances/{id}       # delete (CONFIRM FIRST)
```

---

## Common Config Fields (All Channel Types)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `dm_policy` | string | `"pairing"` | `"pairing"` / `"allowlist"` / `"open"` / `"disabled"` |
| `group_policy` | string | `"pairing"` | Same options as dm_policy (not all channels support groups) |
| `allow_from` | string[] | `[]` | User IDs allowed when policy = `"allowlist"` |
| `require_mention` | bool | `true` | Groups: only respond when @mentioned |
| `history_limit` | int | `50` | Pending messages to buffer in groups before @mention |
| `block_reply` | bool | null | Override gateway `block_reply` setting |

### Policy Explanation
- **`pairing`**: User must "pair" (first message triggers approval flow) before agent responds
- **`allowlist`**: Only user IDs in `allow_from` can interact
- **`open`**: Anyone can interact (use with caution)
- **`disabled`**: Channel direction disabled entirely

---

## Zalo Personal

**Use case:** DMs + Group messaging (unofficial reverse-engineered API)

### Credentials
```json
{
  "imei": "device-imei-string",
  "cookie": [ ... ],
  "userAgent": "browser-user-agent-string",
  "language": "vi"
}
```

> **How to get credentials:** Login via QR code on Dashboard. Credentials are saved automatically.
> Manual: export cookies from browser session at chat.zalo.me

### Config
```json
{
  "dm_policy": "open",
  "group_policy": "open",
  "require_mention": true,
  "history_limit": 50,
  "allow_from": ["user-id-1", "user-id-2"],
  "block_reply": false
}
```

### Capabilities
| Feature | Supported |
|---------|-----------|
| DM (1-on-1) | Yes |
| Group messages | Yes |
| Send text | Yes (max 2000 chars, auto-chunked) |
| Send images | Yes (upload + send) |
| Send files | Yes (upload + send) |
| Typing indicator | Yes |
| Markdown | No (plain text only) |
| @mention gating | Yes (default ON for groups) |

### Group Messaging Setup
To enable an agent to send messages to Zalo groups:
1. Create channel instance with `group_policy: "open"` or `"allowlist"`
2. If `require_mention: true` (default), agent only responds when @mentioned in group
3. Set `require_mention: false` to respond to ALL group messages (high volume!)
4. Agent uses `message` tool to proactively send to groups

### Example: Create Zalo Personal Channel for Group Messaging
```bash
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "ceo-zalo",
    "display_name": "CEO Zalo Channel",
    "channel_type": "zalo_personal",
    "agent_id": "AGENT_UUID_HERE",
    "credentials": {
      "imei": "...",
      "cookie": [...],
      "userAgent": "..."
    },
    "config": {
      "dm_policy": "allowlist",
      "group_policy": "open",
      "require_mention": true,
      "allow_from": ["ceo-user-id"],
      "history_limit": 50
    }
  }' $URL/v1/channel-instances
```

### Important Notes
- **Account ban risk:** Zalo Personal uses unofficial API. Account may be locked/banned.
- **One account = one channel instance.** Cannot share a Zalo account across agents.
- **QR login required:** First-time setup needs QR scan via Dashboard.
- **Credentials expire:** Cookie-based auth may expire. Re-login via Dashboard if disconnected.

---

## Zalo OA (Official Account)

**Use case:** DM only (official API, safer)

### Credentials
```json
{
  "token": "zalo-oa-bot-token"
}
```

### Config
```json
{
  "dm_policy": "open",
  "allow_from": [],
  "webhook_url": "",
  "webhook_secret": "",
  "media_max_mb": 5,
  "block_reply": false
}
```

### Capabilities
| Feature | Supported |
|---------|-----------|
| DM (1-on-1) | Yes |
| Group messages | **No** |
| Send text | Yes (max 2000 chars) |
| Send photos | Yes (URL-based) |
| Send files | No |
| Typing indicator | No |
| Markdown | No |

### Limitations
- **No group support** — Zalo OA API only supports direct messages
- **Photo only** — no file attachments, only photo URLs
- **Polling mode** — default 30s polling, or configure webhook

---

## Telegram

### Credentials
```json
{
  "token": "bot-token-from-botfather",
  "proxy": "socks5://host:port",
  "api_server": "https://custom-api.telegram.org"
}
```

### Config
```json
{
  "dm_policy": "pairing",
  "group_policy": "pairing",
  "require_mention": true,
  "history_limit": 50,
  "dm_stream": false,
  "group_stream": false,
  "reasoning_stream": true,
  "reaction_level": "minimal",
  "media_max_mb": 50,
  "link_preview": true,
  "allow_from": [],
  "force_ipv4": false
}
```

### Capabilities
| Feature | Supported |
|---------|-----------|
| DM | Yes |
| Groups | Yes |
| Streaming (edit messages) | Yes (`dm_stream`, `group_stream`) |
| Thinking/reasoning display | Yes (`reasoning_stream`) |
| Markdown (HTML) | Yes (auto-converted) |
| Media (photos, files, voice) | Yes |
| Reactions | Yes (`reaction_level`: off/minimal/full) |
| Voice-to-text (STT) | Yes (needs STT config) |
| Link preview | Yes (default ON) |

---

## Discord

### Credentials
```json
{
  "token": "discord-bot-token"
}
```

### Config
```json
{
  "dm_policy": "open",
  "group_policy": "pairing",
  "require_mention": true,
  "history_limit": 50,
  "allow_from": [],
  "media_max_bytes": 26214400
}
```

### Capabilities
| Feature | Supported |
|---------|-----------|
| DM | Yes |
| Channels/Threads | Yes |
| Media | Yes (25MB max) |
| Markdown | Yes (Discord flavor) |
| Voice-to-text | Yes (needs STT config) |

### Requirements
- Bot needs **Message Content Intent** enabled in Discord Developer Portal
- Bot needs proper OAuth2 permissions (Send Messages, Read Message History, etc.)

---

## Agent Tools Config Reference

When creating/updating an agent, use `tools_config` to control which tools are available:

### Structure
```json
{
  "profile": "full",
  "deny": ["tool1", "tool2"],
  "allow": ["tool3"],
  "alsoAllow": ["tool4"]
}
```

### Profiles
| Profile | Tools |
|---------|-------|
| `"full"` or `""` | All tools (default) |
| `"coding"` | fs, runtime, sessions, memory, web, images, skill_search |
| `"messaging"` | messaging, web, sessions, read_image, skill_search |
| `"minimal"` | session_status only |

### Key Tools for Messaging Agents
| Tool | Purpose |
|------|---------|
| `message` | Send messages to channels (DM + group) |
| `sessions_list` | List agent's own sessions |
| `sessions_history` | View session message history |
| `sessions_send` | Send message to another session (same agent only) |
| `memory_search` | Search agent's memory |
| `memory_get` | Get memory document |
| `skill_search` | Search available skills |
| `exec` | Execute shell commands (including curl) |
| `web_fetch` | Fetch URLs (GET only) |
| `web_search` | Search the web |

### Example: Agent with Messaging Focus
```json
{
  "tools_config": {
    "profile": "full",
    "deny": ["browser", "create_image", "create_video", "create_audio", "read_video", "read_audio", "tts"]
  }
}
```

---

## Message Tool — How Agents Send Messages

### Parameters
```json
{
  "action": "send",
  "channel": "channel-instance-name",
  "target": "user-id-or-group-id",
  "message": "Hello world"
}
```

- `channel`: optional — defaults to current conversation's channel
- `target`: optional — defaults to current conversation's chat ID
- `message`: required — text content, or `MEDIA:/path/to/file` for attachments

### Proactive Group Messaging
For an agent to send a message to a Zalo group proactively:
1. Agent must have `message` tool enabled (default in `full` profile)
2. Channel instance must be bound to the agent with `group_policy` not `"disabled"`
3. Agent calls: `message(action="send", channel="channel-name", target="group-id", message="content")`
4. The system routes to the correct channel and uses group API

### How Group Routing Works
- System checks if `target` is a known group (from `approvedGroups` cache)
- If target has `group:` or `guild:` prefix → group mode
- Message bus publishes `OutboundMessage` with `metadata: {"group_id": target}`
- Channel handler detects group metadata and uses group send API

---

## Agent other_config Reference

Full structure of `other_config` JSONB field:

```json
{
  "thinking_level": "off",
  "max_tokens": 4096,
  "self_evolve": false,
  "skill_evolve": true,
  "skill_nudge_interval": 20,
  "description": "Agent purpose description",
  "shell_deny_groups": {
    "destructive_ops": true,
    "data_exfiltration": false,
    "reverse_shell": true,
    "code_injection": true,
    "privilege_escalation": true,
    "dangerous_paths": true,
    "env_injection": true,
    "container_escape": true,
    "crypto_mining": true,
    "filter_bypass": true,
    "network_recon": true,
    "package_install": true,
    "persistence": true,
    "process_control": true,
    "env_dump": true
  },
  "workspace_sharing": {
    "shared_dm": false,
    "shared_group": true,
    "shared_users": [],
    "share_memory": false,
    "share_knowledge_graph": false
  }
}
```

| Field | Description |
|-------|-------------|
| `thinking_level` | LLM reasoning: `"off"`, `"low"`, `"medium"`, `"high"` (provider-dependent) |
| `max_tokens` | Max output tokens per LLM call |
| `self_evolve` | Allow agent to modify own SOUL.md |
| `skill_evolve` | Enable learning loop (suggest skill creation from patterns) |
| `skill_nudge_interval` | Nudge every N tool calls (default 15) |
| `shell_deny_groups` | Enable/disable shell command deny groups (true = blocked) |
| `workspace_sharing` | Share workspace/memory across users in DM/group |

---

## Complete Example: Configure Agent for Zalo Group Messaging

### Step 1: Create Agent
```bash
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_key": "ceo-assistant",
    "display_name": "CEO Assistant",
    "provider": "openai",
    "model": "gpt-4o",
    "agent_type": "predefined",
    "context_window": 128000,
    "max_tool_iterations": 20,
    "tools_config": {
      "profile": "full",
      "deny": ["browser", "create_image", "create_video"]
    },
    "other_config": {
      "shell_deny_groups": {
        "destructive_ops": true,
        "data_exfiltration": true,
        "reverse_shell": true,
        "code_injection": true,
        "privilege_escalation": true
      }
    }
  }' $URL/v1/agents
```

### Step 2: Create Zalo Channel Instance (bind to agent)
```bash
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "ceo-zalo-personal",
    "display_name": "CEO Zalo",
    "channel_type": "zalo_personal",
    "agent_id": "AGENT_UUID_FROM_STEP_1",
    "credentials": {
      "imei": "...",
      "cookie": [...],
      "userAgent": "..."
    },
    "config": {
      "dm_policy": "allowlist",
      "group_policy": "open",
      "require_mention": true,
      "allow_from": ["ceo-zalo-user-id"],
      "history_limit": 50
    }
  }' $URL/v1/channel-instances
```

### Step 3: Set Agent Context Files (via Dashboard or SQL)
Configure IDENTITY.md, SOUL.md with instructions for group messaging behavior.

### Step 4: Verify
```bash
# Check agent
curl -s -H "Authorization: Bearer $TOKEN" $URL/v1/agents/ceo-assistant | jq .

# Check channel instance
curl -s -H "Authorization: Bearer $TOKEN" $URL/v1/channel-instances | jq '.[] | select(.name=="ceo-zalo-personal")'

# Test wake
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "Send hello to group", "user_id": "ceo"}' \
  $URL/v1/agents/AGENT_UUID/wake
```

---

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| Agent can't send to group | `group_policy: "disabled"` | Set to `"open"` or `"allowlist"` |
| Agent responds to all group msgs | `require_mention: false` | Set to `true` |
| "channel not found" in message tool | Wrong channel name | Use exact `name` from channel instance |
| Zalo disconnected | Cookie expired | Re-login via Dashboard QR |
| "access denied" on sessions_send | Cross-agent send attempt | Agents can only send to own sessions |
| Message tool missing | Tool denied in tools_config | Check `deny` list, ensure `message` not blocked |
