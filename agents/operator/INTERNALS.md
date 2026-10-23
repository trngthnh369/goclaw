# INTERNALS.md — GoClaw System Internals

> This document contains implementation knowledge that only comes from reading the GoClaw source code.
> You CANNOT read source code — this is your substitute. Trust these details.

---

## Tool System

### web_fetch is GET-only
`web_fetch` is hardcoded to `http.NewRequestWithContext(ctx, "GET", ...)`. It CANNOT make POST/PUT/DELETE requests.
**Always use `exec` + `curl` for any API mutation.**

### SSRF Protection (web_fetch & web_search)
Blocked destinations (web_shared.go):
- `localhost`, `127.0.0.1`, `0.0.0.0`, `::1`
- Private IP ranges: `10.x.x.x`, `172.16-31.x.x`, `192.168.x.x`
- `*.local`, `*.internal`, `metadata.google.internal`

**NOT blocked:** External public domains. `curl` via `exec` has NO SSRF restriction.

### effectiveRestrict() always returns true
In `context_keys.go:176-180`, `effectiveRestrict()` always returns `true` regardless of the agent's `restrict_to_workspace` config. All agents are locked to their workspace directory.

### Shell Deny Groups (15 total, ALL default ON)
| Group | What it blocks |
|-------|---------------|
| `destructive_ops` | rm -rf, format, dd, shutdown, reboot, fork bomb |
| `data_exfiltration` | curl POST/PUT/PATCH, curl\|sh, wget POST, DNS exfil, curl/wget to localhost |
| `reverse_shell` | nc, netcat, socat, openssl s_client, telnet, python/perl/ruby/node socket |
| `code_injection` | eval $, base64 -d \| sh |
| `privilege_escalation` | sudo, su, doas, pkexec, mount, nsenter, capsh |
| `dangerous_paths` | chmod/chown on system paths, +x on /tmp |
| `env_injection` | LD_PRELOAD, DYLD_INSERT_LIBRARIES, BASH_ENV |
| `container_escape` | docker.sock, /proc/sys, /sys/kernel |
| `crypto_mining` | xmrig, stratum+tcp/ssl |
| `filter_bypass` | sed /e, sort --compress-program, git --exec |
| `network_recon` | nmap, ssh @, ngrok, chisel tunnels |
| `package_install` | pip install, npm install, apk add, yarn add |
| `persistence` | crontab, write to .bashrc/.profile |
| `process_control` | kill -9, killall, pkill |
| `env_dump` | env, printenv, /proc/environ, echo $GOCLAW_* |

**Operator has these DISABLED:** `data_exfiltration` (needed for curl POST to APIs).
**Operator has these ENABLED:** `destructive_ops` (via "rm" alias), `credential_theft` (custom), `privilege_escalation`, `network_abuse` (custom), `code_injection`.

### Subagent Deny Lists
**SubagentDenyAlways** (always denied to spawned subagents):
`gateway`, `agents_list`, `whatsapp_login`, `session_status`, `cron`, `memory_search`, `memory_get`, `sessions_send`

**SubagentDenyLeaf** (additionally denied at max spawn depth):
`sessions_list`, `sessions_history`, `sessions_spawn`, `spawn`

**Notable:** `exec` is NOT denied to subagents. Subagents CAN use curl.

### Tool Profiles
| Profile | Tools Allowed |
|---------|--------------|
| `minimal` | session_status only |
| `coding` | fs, runtime, sessions, memory, web, read_image, create_image, skill_search |
| `messaging` | messaging, web, sessions_list/history/send, session_status, read_image, skill_search |
| `full` | No restrictions (all tools) |

Empty or "full" profile = all tools allowed.

---

## Agent Loop

### Context Propagation Order
Injected into context at start of `runLoop`:
1. `store.WithAgentID(ctx, agentUUID)`
2. `store.WithTenantID(ctx, tenantID)`
3. `store.WithUserID(ctx, userID)`
4. `store.WithAgentType(ctx, agentType)`
5. `store.WithSelfEvolve(ctx, true)` (if enabled)

### Compaction (Auto-Summarization)
- **Threshold:** `contextWindow * historyShare` where `DefaultHistoryShare = 0.75`
- **MinMessages:** default 200 (Operator set to 300)
- **KeepLastMessages:** default 4 messages retained after compaction
- **Process:** Token estimation → threshold check → per-session lock (TryLock, non-blocking) → memory flush → summarize in background goroutine
- **Timeout:** 120 seconds for summarization
- **Mid-loop compaction:** Also runs between tool iterations using same threshold
- Concurrent runs skip if another summarization is in progress (non-blocking TryLock)

### Memory Flush (Pre-Compaction)
Before compaction summarizes history, a memory flush runs:
1. Check if memory is enabled for agent
2. Dedup guard: skip if already flushed in this compaction cycle (tracked by `compactionCount`)
3. LLM call to extract important information from conversation
4. **Extractive fallback:** If LLM doesn't respond or returns NO_REPLY, auto-extracts context to prevent loss
5. Writes to agent's memory/ directory

### Max Tool Iterations
When agent reaches `max_tool_iterations`, the loop stops. The agent's response is truncated — no graceful exit. Operator is set to 40 iterations.

### Skill Resolution
Skills are loaded from multiple sources in priority order:
1. `workspace/skills/` (workspace-local)
2. `.agents/skills/` (agent-local)
3. `~/.agents/skills/` (user-local)
4. Managed skills-store (Docker: `/app/data/skills-store/{slug}/{version}/SKILL.md`)
5. Built-in skills

Skill search uses BM25 ranking. `skill_evolve: true` enables agents to suggest skill creation from repeated patterns (`skill_nudge_interval: 20` = nudge every 20 messages).

---

## Provider System

### Provider Types
| Type | Protocol | API Base |
|------|----------|----------|
| `anthropic_native` | HTTP + SSE | `https://api.anthropic.com` |
| `openai_compat` | HTTP + SSE | varies |
| `gemini_native` | HTTP + SSE | `https://generativelanguage.googleapis.com` |
| `openrouter` | OpenAI compat | `https://openrouter.ai/api/v1` |
| `groq` | OpenAI compat | `https://api.groq.com/openai/v1` |
| `deepseek` | OpenAI compat | `https://api.deepseek.com/v1` |
| `mistral` | OpenAI compat | `https://api.mistral.ai/v1` |
| `xai` | OpenAI compat | `https://api.x.ai/v1` |
| `ollama` | OpenAI compat | `http://localhost:11434/v1` |
| `dashscope` | Alibaba Qwen | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `codex` | OpenAI | OpenAI Codex |
| `claude_cli` | stdio + MCP bridge | local CLI |

### RetryDo() — Automatic Retry
- **Default config:** 3 attempts, 300ms initial delay, 30s max delay, ±10% jitter
- **Retryable errors:** HTTP 429, 500, 502, 503, 504, network errors, connection reset, broken pipe, EOF, timeout
- **NOT retryable:** HTTP 400, 401, 403, 404 (client errors)
- **Backoff:** Exponential: `minDelay * 2^(attempt-1)`, capped at maxDelay
- **Retry-After:** Honors `Retry-After` header from provider (overrides computed delay)
- **Context cancellation:** Respects ctx.Done() between retries

---

## HTTP API Internals

### Error Response Format
All API errors return JSON: `{"error": "message"}` with appropriate HTTP status code.
Exception: `/v1/chat/completions` uses OpenAI format: `{"error": {"message": "...", "type": "invalid_request_error"}}`

### Authentication
- Gateway token: `Authorization: Bearer {GOCLAW_GATEWAY_TOKEN}`
- API key: `Authorization: Bearer {api_key}` (scoped by API key permissions)
- No auth configured = all requests allowed (dev mode)
- Failed auth: HTTP 401 `{"error": "unauthorized"}`

### Rate Limiting
**Gateway level:** Per-user/IP token bucket (requests per minute). Configurable via `rate_limit_rpm` and `rate_limit_burst` in config.
- Default burst: 5 if not set
- Disabled if rpm <= 0
- Stale entries cleaned every 5 minutes (entries older than 10 minutes)
- Rate limited: HTTP 429 with OpenAI-style error

**Tool level:** Per-agent:user sliding window rate limiter (max actions per hour). Returns error string if exceeded.

### Context File Editing via HTTP
`PUT /v1/agents/{id}/instances/{userID}/files/{fileName}` — **ONLY allows `USER.md`** (or `USER_PREDEFINED.md` for predefined agents). Other files (IDENTITY.md, SOUL.md, etc.) must be set via Dashboard or direct SQL.

### Owner Visibility
`GOCLAW_OWNER_IDS` env var → comma-separated user IDs. `isOwnerUser()` check → owners see ALL agents. Non-owners only see: own agents, shared agents, default agents.

### Session History
Session message history is ONLY available via WebSocket RPC `sessions.preview` method. There is NO REST HTTP endpoint for reading session messages. The `GET /v1/sessions` endpoint only lists sessions (metadata), not their content.

---

## Agent Types

### Open Agents
- Per-user context: 7 personal files that each user can customize
- Each user has their own version of context files
- Best for: personal assistants, customizable chatbots

### Predefined Agents
- Shared context files (IDENTITY.md, SOUL.md, etc.) set by admin
- `USER.md` per-user (auto-created from `USER_PREDEFINED.md` template)
- Admin controls behavior; users only customize USER.md
- Best for: company bots, support agents, Operator

---

## Security Details

### Master Tenant ID
`0193a5b0-7000-7000-8000-000000000001` — hardcoded in seed scripts. All master-level resources belong to this tenant.

### API Key Scopes
`operator.admin`, `operator.read`, `operator.write`, `operator.approvals`, `operator.pairing`, `operator.provision`

### Input Guard
Detection-only mode — logs security events but does NOT block requests. Logged as `slog.Warn("security.*")`.

---

## Cron System

### Job Types
| Type | Format | Example |
|------|--------|---------|
| `at` | `{"kind":"at","atMs":<unix_ms>}` | One-time at specific timestamp |
| `every` | `{"kind":"every","everyMs":<ms>}` | Recurring interval in milliseconds |
| `cron` | `{"kind":"cron","expr":"<5-field>","tz":"<timezone>"}` | Standard 5-field cron expression |

### Cron Execution
Jobs trigger the agent with the configured message. `deleteAfterRun: true` for one-time "at" jobs.

---

## Memory System

### Document Lifecycle
1. **Upload:** `PUT /v1/agents/{id}/memory/documents/{path}` — stores text content
2. **Index:** `POST /v1/agents/{id}/memory/index` — creates embedding vectors (requires embedding provider)
3. **Search:** `POST /v1/agents/{id}/memory/search` — semantic search over indexed chunks
4. **Re-index:** `POST /v1/agents/{id}/memory/index-all` — re-index all documents

### Knowledge Graph
Entities with observations and relationships. Uses `POST /v1/agents/{id}/kg/entities` to create, `POST /v1/agents/{id}/kg/traverse` to navigate relationships.

### Embedding Requirement
Memory indexing requires a working embedding model. If the agent's provider doesn't support embeddings, indexing will fail silently or with error.

---

## Docker Network

Inside Docker Compose, services communicate via service names:
- GoClaw server: `goclaw:18790`
- PostgreSQL: `goclaw-postgres:5432`
- The `GOCLAW_GATEWAY_TOKEN` env var is available in the agent's shell environment

When accessing from outside Docker (remote management), use the public URL/IP.
