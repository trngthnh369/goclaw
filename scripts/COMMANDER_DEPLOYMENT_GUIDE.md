# GoClaw Commander — Hướng dẫn triển khai Admin Agent

> Tài liệu này hướng dẫn cấu hình **Commander** — admin agent cho GoClaw.
> Commander cho phép quản lý toàn bộ hệ thống GoClaw qua hội thoại tự nhiên.

---

## 1. Tổng quan kiến trúc

```
┌──────────────────────────────────────────────────────────────┐
│                     GoClaw Server                            │
│                                                              │
│  ┌─────────────┐    HTTP API (port 18790)    ┌───────────┐  │
│  │  Commander   │ ──── curl/web_fetch ──────> │  Agents   │  │
│  │  (Admin AI)  │                             │  Providers│  │
│  │              │                             │  Channels │  │
│  │  Tools:      │                             │  MCP      │  │
│  │  - exec      │                             │  Skills   │  │
│  │  - web_fetch │                             │  Config   │  │
│  │  - memory    │                             │  API Keys │  │
│  │  - cron      │                             └───────────┘  │
│  │  - sessions  │                                            │
│  │  - message   │    ┌─────────┐  ┌──────────────────────┐  │
│  │  - skills    │    │ Discord │  │ Telegram/Zalo/Slack  │  │
│  └──────┬───────┘    └────┬────┘  └──────────┬───────────┘  │
│         │                 │                   │              │
│         └────── Owner chats via channel ──────┘              │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │              PostgreSQL + pgvector                     │   │
│  │  agents | providers | channels | mcp | skills | ...   │   │
│  └──────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────┘
```

### Nguyên lý hoạt động

Commander là một **predefined agent** chạy trong GoClaw, sử dụng HTTP API nội bộ (`localhost:18790`) để quản lý các tài nguyên hệ thống. Agent này:

- Gọi API qua `exec` (curl) hoặc `web_fetch` tool
- Xác thực bằng `GOCLAW_GATEWAY_TOKEN`
- Tuân theo safety rules trong context files (không thể bị override bởi user)
- Ghi nhớ preferences qua memory system
- Chỉ cho phép owner truy cập (owner-only)

---

## 2. Yêu cầu hệ thống

| Thành phần | Yêu cầu |
|---|---|
| GoClaw | Version mới nhất, đang chạy trên Docker |
| PostgreSQL | pgvector enabled, đã migrate schema mới nhất |
| LLM Provider | Ít nhất 1 provider đã cấu hình (khuyến nghị: Gemini 2.5 Pro hoặc GPT-5.4) |
| Gateway Token | Đã set `GOCLAW_GATEWAY_TOKEN` trong env |
| Network | Commander container cần truy cập được `localhost:18790` hoặc `goclaw:18790` |

---

## 3. Triển khai

### Bước 1: Chạy SQL seed script

```bash
# Từ host machine
docker exec -i <postgres-container> psql -U goclaw -d goclaw < scripts/commander-setup.sql

# Hoặc nếu dùng docker compose
docker compose exec -T postgres psql -U goclaw -d goclaw < scripts/commander-setup.sql
```

Script sẽ tạo:
- 1 agent: `goclaw-commander` (predefined, Gemini 2.5 Pro)
- 5 context files: IDENTITY.md, SOUL.md, ADMIN_GUIDE.md, SAFETY_RULES.md, USER_PREDEFINED.md

### Bước 2: Tuỳ chỉnh cho môi trường công ty

**Đổi provider/model** (sửa trong SQL trước khi chạy):

```sql
-- Dòng 10-11 trong commander-setup.sql
provider_name text := 'gemini';                        -- Đổi thành provider bạn muốn
model_name text := 'gemini-2.5-pro-preview-05-06';     -- Đổi thành model bạn muốn
```

Các lựa chọn provider phổ biến:

| Provider | Model khuyến nghị | Chi phí | Ghi chú |
|---|---|---|---|
| `gemini` | `gemini-2.5-pro-preview-05-06` | Thấp | Tốt nhất cho admin tasks, context window lớn |
| `openai` | `gpt-5.4` | Cao | Chính xác cao, tool calling mạnh |
| `openai` | `gpt-5.4-mini` | Trung bình | Tiết kiệm, đủ cho admin cơ bản |
| `anthropic` | `claude-sonnet-4-6` | Trung bình | Cân bằng chất lượng/chi phí |
| `openrouter` | Bất kỳ | Tuỳ model | Linh hoạt chọn model |

### Bước 3: Kết nối channel (tuỳ chọn)

**Option A: Dùng qua Web UI (WebSocket)**
- Mở GoClaw Web UI → chọn agent "Commander" → chat trực tiếp
- Không cần cấu hình thêm

**Option B: Discord channel riêng cho admin**
```bash
# Gọi API tạo channel instance
curl -X POST http://localhost:18790/v1/channel-instances \
  -H "Authorization: Bearer $GOCLAW_GATEWAY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "commander-discord",
    "display_name": "Commander Admin Channel",
    "channel_type": "discord",
    "agent_id": "<commander-agent-uuid>",
    "credentials": {"bot_token": "<DISCORD_BOT_TOKEN>"},
    "config": {
      "allowed_guilds": ["<YOUR_GUILD_ID>"],
      "admin_channel_id": "<PRIVATE_CHANNEL_ID>"
    },
    "enabled": true
  }'
```

**Option C: Telegram bot cho admin**
```bash
curl -X POST http://localhost:18790/v1/channel-instances \
  -H "Authorization: Bearer $GOCLAW_GATEWAY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "commander-telegram",
    "display_name": "Commander Telegram",
    "channel_type": "telegram",
    "agent_id": "<commander-agent-uuid>",
    "credentials": {"bot_token": "<TELEGRAM_BOT_TOKEN>"},
    "config": {},
    "enabled": true
  }'
```

### Bước 4: Xác minh

```bash
# Kiểm tra agent đã tạo thành công
curl -s http://localhost:18790/v1/agents \
  -H "Authorization: Bearer $GOCLAW_GATEWAY_TOKEN" | \
  python3 -m json.tool | grep commander

# Kiểm tra context files
docker exec <postgres-container> psql -U goclaw -d goclaw -c \
  "SELECT file_name, length(content) FROM agent_context_files 
   WHERE agent_id = (SELECT id FROM agents WHERE agent_key='goclaw-commander');"
```

---

## 4. Cấu hình chi tiết

### 4.1 Tools Configuration

Commander mặc định được cấu hình với tools phù hợp admin:

| Tool | Status | Mục đích |
|---|---|---|
| `exec` | ✅ Enabled | Chạy curl gọi HTTP API |
| `web_fetch` | ✅ Enabled | Gọi API (alternative cho exec) |
| `read_file` | ✅ Enabled | Đọc config files, logs |
| `write_file` | ✅ Enabled | Viết config files |
| `edit` | ✅ Enabled | Sửa files |
| `list_files` | ✅ Enabled | Liệt kê workspace |
| `memory_search` | ✅ Enabled | Tìm trong memory (admin preferences) |
| `memory_get` | ✅ Enabled | Đọc memory documents |
| `sessions_list` | ✅ Enabled | Xem sessions đang active |
| `session_status` | ✅ Enabled | Kiểm tra session cụ thể |
| `sessions_history` | ✅ Enabled | Xem lịch sử chat |
| `sessions_send` | ✅ Enabled | Gửi message vào session |
| `message` | ✅ Enabled | Gửi thông báo proactive |
| `cron` | ✅ Enabled | Lên lịch task định kỳ |
| `spawn` | ✅ Enabled (limited) | Spawn subagent (maxDepth=1) |
| `skill_search` | ✅ Enabled | Tìm skills |
| `skill_manage` | ✅ Enabled | Quản lý skills |
| `web_search` | ✅ Enabled | Tìm kiếm web (tra cứu docs) |
| `knowledge_graph_search` | ✅ Enabled | Tìm trong knowledge graph |
| `browser` | ❌ Denied | Không cần cho admin |
| `create_image` | ❌ Denied | Không cần |
| `create_video` | ❌ Denied | Không cần |
| `create_audio` | ❌ Denied | Không cần |
| `read_video` | ❌ Denied | Không cần |
| `read_audio` | ❌ Denied | Không cần |
| `tts` | ❌ Denied | Không cần |

**Tuỳ chỉnh tools** (qua API):

```bash
# Thêm deny cho exec nếu muốn restrict hơn
curl -X PUT http://localhost:18790/v1/agents/<commander-id> \
  -H "Authorization: Bearer $GOCLAW_GATEWAY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "tools_config": {
      "deny": ["browser", "create_image", "create_video", "create_audio", "read_video", "read_audio", "tts", "spawn"]
    }
  }'
```

### 4.2 Memory Configuration

Commander có memory enabled để ghi nhớ:
- Admin preferences (output format, confirmation level)
- Lịch sử thay đổi cấu hình
- Ghi chú về agents/channels
- Provider notes (model nào hoạt động tốt, model nào có vấn đề)

### 4.3 Subagent Configuration

| Setting | Value | Lý do |
|---|---|---|
| `maxSpawnDepth` | 1 | Admin không cần delegation chain sâu |
| `maxConcurrent` | 2 | Giới hạn tải parallel |
| `maxChildrenPerAgent` | 3 | Đủ cho parallel admin tasks |

### 4.4 Budget Control

Thêm giới hạn chi phí hàng tháng:

```bash
# Set budget 50 USD/tháng cho Commander
curl -X PUT http://localhost:18790/v1/agents/<commander-id> \
  -H "Authorization: Bearer $GOCLAW_GATEWAY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"budget_monthly_cents": 5000}'
```

---

## 5. Security Checklist

### Trước khi deploy lên production

- [ ] **Gateway token mạnh**: Đảm bảo `GOCLAW_GATEWAY_TOKEN` là random string dài (>= 32 chars)
- [ ] **Encryption key**: `GOCLAW_ENCRYPTION_KEY` phải là hex string 64 chars (256-bit)
- [ ] **Owner-only access**: Commander chỉ nên respond với owner IDs đã cấu hình
- [ ] **Channel isolation**: Nếu dùng Discord/Telegram, giới hạn channel IDs cụ thể
- [ ] **Network isolation**: Commander container không cần truy cập internet trực tiếp
- [ ] **Restrict workspace**: Cân nhắc set `restrict_to_workspace: true` nếu không cần đọc file ngoài workspace
- [ ] **Audit logging**: Đảm bảo `activity_logs` table đang ghi log
- [ ] **No raw SQL**: Commander chỉ dùng HTTP API, không truy cập trực tiếp database
- [ ] **Budget limit**: Set `budget_monthly_cents` để tránh chi phí đột biến
- [ ] **Test trước**: Chạy thử các lệnh read-only trước khi cho phép write

### Security layers có sẵn trong GoClaw

```
Layer 1: Gateway Auth (token/password + scopes)
    ↓
Layer 2: Global Tool Policy (tools.allow/deny)
    ↓
Layer 3: Per-Agent Tool Policy (commander tools_config.deny)
    ↓
Layer 4: Per-Channel/Group Policy (channel config)
    ↓
Layer 5: Owner-Only Check (senderIsOwner)
    ↓
Layer 6: Safety Rules (SAFETY_RULES.md — LLM instruction)
    ↓
Layer 7: Exec Shell Deny Groups (block dangerous commands)
    ↓
Layer 8: Rate Limiter (per-tool rate limiting)
    ↓
Layer 9: Credential Scrubbing (auto-mask secrets in output)
    ↓
Layer 10: Audit Logging (activity_logs table)
```

---

## 6. Sử dụng Commander — Ví dụ thực tế

### Quản lý Agent

```
👤 User: Tạo agent mới tên "customer-support", dùng GPT-5.4-mini, connect Discord
⚙️ Commander: 
  Mình sẽ thực hiện 3 bước:
  1. Tạo agent "customer-support"
  2. Seed context files
  3. Tạo Discord channel instance
  
  Bắt đầu bước 1...
  ✅ Agent created: customer-support (id: 019d...)
  ✅ Context files seeded
  ✅ Discord channel created
  
  Agent đã sẵn sàng. Bạn muốn cấu hình thêm gì không?
```

### Quản lý Provider

```
👤 User: Thêm Anthropic provider với API key sk-ant-xxx
⚙️ Commander:
  ✅ Provider "anthropic" created
  ⚙️ Verifying connection... ✅ OK (claude-sonnet-4-6 responds)
  
  Available models:
  | Model | Context | Max Output |
  |-------|---------|------------|
  | claude-opus-4-6 | 200K | 32K |
  | claude-sonnet-4-6 | 200K | 16K |
  | claude-haiku-4-5 | 200K | 8K |
```

### Monitoring

```
👤 User: Kiểm tra tình trạng hệ thống
⚙️ Commander:
  | Component | Status |
  |-----------|--------|
  | Gateway | ✅ Healthy |
  | Agents | 15 active |
  | Providers | 4 enabled |
  | Channels | 5 connected |
  | Sessions | 23 active |
  | MCP | 2 servers |
  
  Token usage (24h):
  | Provider | Input | Output | Est. Cost |
  |----------|-------|--------|-----------|
  | Gemini | 1.2M | 340K | $0.82 |
  | OpenAI | 890K | 210K | $3.45 |
```

### Cron Job

```
👤 User: Set up health check mỗi 6 tiếng, notify tôi qua Discord nếu có vấn đề
⚙️ Commander:
  ✅ Cron job created: "system-health-check"
  - Schedule: every 6h
  - Action: Check /health endpoint + agent status
  - Alert: Discord message to admin channel if any issues
```

---

## 7. Tuỳ chỉnh cho công ty

### Thay đổi ngôn ngữ

Sửa SOUL.md → section `Style` → `Language`:
```
- **Language:** Vietnamese (default), English when technical terms needed
```

### Thêm company-specific rules

Thêm vào SAFETY_RULES.md:
```markdown
## Company Rules
- Production agents MUST use gemini provider (cost control)
- New agents require approval from team lead
- API keys expire after 90 days
- All channels must have allowed_guilds/chat_ids configured
```

### Multi-tenant deployment

Nếu công ty có nhiều team/project dùng chung GoClaw:

1. Tạo separate tenants cho mỗi team
2. Tạo Commander riêng cho mỗi tenant (sửa `tid` trong SQL)
3. Mỗi Commander chỉ quản lý tenant của mình

```sql
-- Thay dòng tenant_id trong SQL
tid uuid := '<your-tenant-uuid>';
```

### Kết nối với monitoring bên ngoài

Commander có thể tích hợp với:
- **Grafana/Prometheus**: Dùng cron job gọi metrics endpoint
- **Slack/Teams**: Dùng `message` tool hoặc `web_fetch` webhook
- **PagerDuty**: Gọi PagerDuty API qua `exec` curl khi có incident

---

## 8. Troubleshooting

| Vấn đề | Nguyên nhân | Giải pháp |
|---|---|---|
| Commander không respond | Agent chưa active | Check `SELECT status FROM agents WHERE agent_key='goclaw-commander'` |
| API calls fail 401 | Token sai | Kiểm tra `GOCLAW_GATEWAY_TOKEN` env var |
| API calls fail 404 | URL sai | Dùng `http://localhost:18790` (host) hoặc `http://goclaw:18790` (Docker) |
| Memory không hoạt động | Missing embedding provider | Đảm bảo có provider support embeddings (OpenAI, Gemini) |
| Context files trống | Seed chưa chạy | Chạy lại SQL script |
| Tool bị deny | tools_config.deny | Kiểm tra `tools_config` trong agents table |

### Reset Commander

```bash
# Xoá và tạo lại (cẩn thận — mất memory)
docker exec -i <postgres-container> psql -U goclaw -d goclaw -c \
  "DELETE FROM agents WHERE agent_key='goclaw-commander';"

# Chạy lại seed
docker exec -i <postgres-container> psql -U goclaw -d goclaw < scripts/commander-setup.sql
```

---

## 9. Tổng kết

### Capabilities Matrix

| Khả năng | Hỗ trợ | Cách thực hiện |
|---|---|---|
| Tạo/sửa/xoá agents | ✅ | HTTP API `/v1/agents` |
| Quản lý LLM providers | ✅ | HTTP API `/v1/providers` |
| Quản lý channels | ✅ | HTTP API `/v1/channel-instances` |
| Quản lý MCP servers | ✅ | HTTP API `/v1/mcp/servers` |
| Quản lý skills | ✅ | HTTP API `/v1/skills` + tools |
| Bật/tắt builtin tools | ✅ | HTTP API `/v1/tools/builtin` |
| API key management | ✅ | HTTP API `/v1/api-keys` |
| Cron scheduling | ✅ | `cron` tool |
| Session monitoring | ✅ | `sessions_list` tool |
| Health monitoring | ✅ | HTTP `/health` + cron |
| Memory/preferences | ✅ | Memory tools |
| Multi-tenant | ✅ | Tenant-scoped queries |
| Gateway config (JSON5) | ⚠️ Hạn chế | Cần WS RPC (không phải HTTP) |
| Database direct access | ❌ Intentionally blocked | Security by design |
| Docker management | ❌ Intentionally blocked | Security by design |
| OS-level operations | ❌ Intentionally blocked | Security by design |

### File manifest

```
scripts/
├── commander-setup.sql            # SQL seed script (agent + context files)
└── COMMANDER_DEPLOYMENT_GUIDE.md  # Tài liệu này
```

---

_Tài liệu tạo ngày 2026-03-31. Tương thích GoClaw version hiện tại._
