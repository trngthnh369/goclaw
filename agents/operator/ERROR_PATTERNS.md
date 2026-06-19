# ERROR_PATTERNS.md — Error Recognition & Recovery Guide

> When an API call fails, match the error against this document BEFORE asking the user.
> Most errors have a known cause and fix.

---

## HTTP Status Code Reference

### 400 Bad Request
**Cause:** Invalid request body, missing required fields, or validation failure.

Common triggers:
- Missing `Content-Type: application/json` header on POST/PUT
- Invalid JSON syntax (unescaped quotes, trailing commas)
- Missing required field (e.g., `agent_key` when creating agent)
- Invalid slug format for `agent_key` (must be lowercase alphanumeric + hyphens)
- Invalid UUID format for ID fields

**Error format:** `{"error": "invalid request: <details>"}`

**Fix:** Check request body syntax. Verify all required fields. Use `jq` to validate JSON before sending.

---

### 401 Unauthorized
**Cause:** Missing or invalid authentication token.

**Error format:** `{"error": "unauthorized"}`
For chat/completions: `{"error": {"message": "invalid or missing authentication", "type": "invalid_request_error"}}`

**Fix:**
1. Check `Authorization: Bearer <token>` header is present
2. Verify token matches `GOCLAW_GATEWAY_TOKEN` env var
3. If using API key, verify key exists and is not revoked: `GET /v1/api-keys`
4. For remote servers: verify `$REMOTE_TOKEN` is correct

---

### 403 Forbidden
**Cause:** Authenticated but insufficient permissions.

Common triggers:
- Non-owner trying to update/delete agent they don't own
- API key with insufficient scopes (e.g., `operator.read` trying to create)
- Trying to access agent not shared with you

**Error format:** `{"error": "only the owner can <action>"}`  or  `{"error": "no access to <resource>"}`

**Fix:**
1. Check if user is the agent owner: `GET /v1/agents/{id}` → `owner_id`
2. Check API key scopes: needs `operator.write` for mutations, `operator.admin` for admin ops
3. Ensure `GOCLAW_OWNER_IDS` includes the user for full visibility

---

### 404 Not Found
**Cause:** Resource doesn't exist or wrong identifier format.

**Error format:** `{"error": "agent not found: <id>"}`

**Common mistakes:**
- Using `agent_key` (string) where UUID is required, or vice versa
- Agent was deleted or never created
- Typo in agent_key or UUID

**Fix:**
1. List resources to find correct ID: `GET /v1/agents`, `GET /v1/providers`, etc.
2. Most endpoints accept both UUID and agent_key — try the other format
3. Check if resource belongs to a different tenant

---

### 409 Conflict
**Cause:** Duplicate resource.

**Error format:** `{"error": "agent already exists: <key>"}`

**Common trigger:** Creating agent with `agent_key` that already exists.

**Fix:**
1. Use a different `agent_key`
2. Or update the existing agent: `PUT /v1/agents/{id}`
3. Or delete first (with confirmation): `DELETE /v1/agents/{id}`

---

### 429 Too Many Requests
**Cause:** Rate limit exceeded (per-user/IP token bucket).

**Error format:** `{"error": {"message": "rate limit exceeded", "type": "rate_limit_error"}}`

**Fix:**
1. Wait and retry (exponential backoff recommended)
2. Rate limit config: `rate_limit_rpm` (requests per minute), `rate_limit_burst` (max burst)
3. Default burst is 5 if not configured
4. If consistently hitting limits, ask admin to increase `rate_limit_rpm` in config

---

### 500 Internal Server Error
**Cause:** Unexpected server error.

**Error format:** `{"error": "<internal error message>"}`

**Fix:**
1. Check server health: `GET /health`
2. Check server logs if accessible
3. Retry once — may be transient
4. If persistent: check database connectivity, provider status

---

## Provider Errors

### Provider Verify Failed
When `POST /v1/providers/{id}/verify` fails:

| Error | Cause | Fix |
|-------|-------|-----|
| "invalid api key" / 401 | Wrong API key | Update provider with correct key |
| "connection refused" | Wrong api_base URL | Check URL and port |
| "timeout" | Provider unreachable | Check network, try again |
| "rate limited" / 429 | Provider rate limit | Wait and retry |
| "model not found" | Invalid model name | List models: `GET /v1/providers/{id}/models` |

### Provider Errors During Agent Run
Logged in traces (`GET /v1/traces`):

| Error | Cause | Fix |
|-------|-------|-----|
| HTTP 429 from provider | Provider rate limit | RetryDo() handles this automatically (3 attempts). If persistent: switch model or wait |
| HTTP 500/502/503 from provider | Provider outage | RetryDo() retries. If persistent: switch to backup provider |
| HTTP 400 from provider | Invalid request (context too large, unsupported feature) | Reduce context_window, check model compatibility |
| HTTP 401 from provider | API key expired/revoked | Update provider API key |
| "context length exceeded" | Input too large for model | Reduce context_window setting, trigger compaction |

### RetryDo() Behavior
Automatic retry on: 429, 500, 502, 503, 504, network errors, timeouts.
NOT retried: 400, 401, 403, 404 (client errors — these are permanent failures).
Config: 3 attempts, 300ms→600ms→1200ms backoff (exponential), ±10% jitter, 30s max delay.
Honors `Retry-After` header from provider.

---

## Agent Run Errors

### Max Iterations Reached
**Symptom:** Agent response seems truncated or incomplete.
**Cause:** Agent used all `max_tool_iterations` without finishing.
**Fix:** Increase `max_tool_iterations` in agent config (Operator is at 40). Or simplify the task.

### Context Overflow
**Symptom:** Provider returns "context length exceeded" error.
**Cause:** Conversation history + system prompt + tool results exceed model's context window.
**Fix:**
1. Compaction should trigger automatically at 75% context usage
2. Increase `context_window` if model supports more
3. Delete and recreate session: `DELETE /v1/sessions/{key}`
4. Reduce number of tools (fewer tool descriptions = smaller system prompt)

### Compaction Loop
**Symptom:** Agent keeps summarizing but never finishes responding.
**Cause:** Summary itself is too large, immediately triggers another compaction.
**Fix:** Increase `compaction_config.minMessages` or increase `context_window`.

---

## Channel Errors

### Telegram
| Error | Cause | Fix |
|-------|-------|-----|
| "Unauthorized" | Invalid bot token | Get new token from @BotFather |
| "bot was blocked by user" | User blocked the bot | Cannot fix — user must unblock |
| "chat not found" | Bot not added to group | Add bot to the group |
| "message is too long" | Response > 4096 chars | GoClaw auto-chunks, but edge cases exist |

### Discord
| Error | Cause | Fix |
|-------|-------|-----|
| "Invalid token" | Wrong bot token | Check Discord Developer Portal |
| "Missing Permissions" | Bot lacks permissions | Grant Message Content intent + required perms |
| "Unknown Channel" | Bot not in server/channel | Invite bot with correct OAuth2 URL |

### Zalo
| Error | Cause | Fix |
|-------|-------|-----|
| "Invalid OA" | Wrong OA credentials | Re-authenticate with Zalo OA |
| Token expired | Refresh token expired | Re-authenticate (Zalo tokens expire) |

---

## Common curl Mistakes

### Missing Content-Type
```bash
# WRONG — server receives empty body
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -d '{"key":"value"}' $URL/v1/agents

# CORRECT — always include Content-Type for POST/PUT
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"key":"value"}' $URL/v1/agents
```

### JSON Escaping in Shell
```bash
# WRONG — shell interprets double quotes
curl -d "{"key":"value"}" ...

# CORRECT — use single quotes for JSON
curl -d '{"key":"value"}' ...

# CORRECT — or escape double quotes
curl -d "{\"key\":\"value\"}" ...

# For nested JSON with single quotes:
curl -d '{"config":{"nested":"value"}}' ...
```

### Missing -s Flag
Without `-s`, curl shows progress bar which pollutes the output. Always use `curl -s`.

### Forgetting Auth Header
Every API call (except `/health`) requires: `-H "Authorization: Bearer $TOKEN"`

### Wrong URL
- Inside Docker: `http://goclaw:18790`
- Outside Docker (local): `http://localhost:18790`
- Remote server: `https://company-server.com:18790`

---

## Memory / Knowledge Errors

### "embedding provider not configured"
**Cause:** Trying to index documents without an embedding-capable provider.
**Fix:** Ensure agent's provider supports embeddings, or configure a dedicated embedding provider.

### Search Returns Empty After Upload
**Cause:** Documents uploaded but NOT indexed.
**Fix:** Run `POST /v1/agents/{id}/memory/index` or `POST /v1/agents/{id}/memory/index-all`

### "document not found"
**Cause:** Wrong path in document URL.
**Fix:** List documents first: `GET /v1/agents/{id}/memory/documents`, then use exact path.

---

## Recovery Procedures

### Server Unresponsive
1. `GET /health` — if timeout, server is down
2. Check Docker: `docker ps` → verify goclaw container running
3. Check logs: `docker logs goclaw-1 --tail 100`
4. Restart if needed: `docker restart goclaw-1` (confirm with user)

### Database Connection Error
1. Check PostgreSQL: `docker exec goclaw-postgres-1 pg_isready`
2. Verify connection string in env vars
3. Check disk space: `docker exec goclaw-postgres-1 df -h`

### Agent Stuck in Running State
1. `GET /v1/sessions?agent_id={id}` — find stuck sessions
2. `DELETE /v1/sessions/{key}` — clear stuck session (confirm first)
3. Test with fresh message: `POST /v1/agents/{id}/wake`

### Lost Configuration
1. Check memory: `POST /v1/tools/invoke` with `tool=memory_search, args={"query":"config"}`
2. Check context files: `GET /v1/agents/{id}/instances/{user}/files`
3. Reference `scripts/commander-setup.sql` for original Operator config
