#!/usr/bin/env bash
# daily-report-send.sh — auto-send end-of-day work report.
#
# Flow:  [LOCAL] GoClaw agent "daily-reporter" (Dayo, claude-cli) analyzes the last 24h
#        of Claude Code sessions and returns a concise Vietnamese text report
#   ->   [SERVER 10.0.0.52] openclaw (andy / zalo_listening) sends it to the user's Zalo DM.
#
# No human review (auto-send, matching the existing broadcast-*.sh pattern).
# Runs on the HOST (git-bash/WSL) so it can reach both the local Docker GoClaw API and SSH.
# Schedule at ~17:10 via Windows Task Scheduler (see daily-report-send.README).
set -uo pipefail

# --- config ---------------------------------------------------------------
GOCLAW_URL="${GOCLAW_URL:-http://127.0.0.1:18790}"
GOCLAW_CONTAINER="${GOCLAW_CONTAINER:-goclaw-goclaw-1}"
AGENT="daily-reporter"
USER_ID="trngthnh369"
SSH_TARGET="${SSH_TARGET:-admin1@10.0.0.52}"
OPENCLAW_CLI="/home/admin1/.npm-global/bin/openclaw"
ZALO_ACCOUNT="zalo_listening"          # account that can DM the user (default=Andy CEO is pairing-gated)
ZALO_TARGET="user:367617605136702044"  # Trường Thịnh
LOG="${LOG:-$HOME/.goclaw-daily-report.log}"

log(){ echo "[$(date '+%F %T')] $*" | tee -a "$LOG" >&2; }

# --- 1. token (read from container; never hard-code) ----------------------
TOKEN=$(docker exec "$GOCLAW_CONTAINER" printenv GOCLAW_GATEWAY_TOKEN 2>/dev/null)
[ -z "$TOKEN" ] && { log "FATAL: cannot read GOCLAW_GATEWAY_TOKEN from $GOCLAW_CONTAINER"; exit 1; }

# --- 2. analysis: ask Dayo for a concise text report ----------------------
PROMPT='Auto-send mode (KHÔNG ghi state, KHÔNG gọi message tool, KHÔNG gửi đi đâu). Chạy digest: python3 /app/data/skills/daily-report/digest_sessions.py --projects-dir /app/.claude-host/projects --hours 24 --max-bytes 60000 . Kiểm health: nếu mount_status != "ok" thì CHỈ trả về đúng chuỗi "REPORT_ERROR: <lý do>" và dừng. Nếu OK, phân tích công việc 24h và TRẢ VỀ báo cáo text tiếng Việt CÔ ĐỌNG (<=1800 ký tự, tiêu đề có ngày, mỗi dự án 1 dòng: tên — tiến độ (✅/🔄/⛔) — tóm tắt). Chỉ trả text báo cáo, không lời dẫn.'

REQ=$(python -c 'import json,sys; print(json.dumps({"model":"agent:'"$AGENT"'","stream":False,"messages":[{"role":"user","content":sys.argv[1]}]}))' "$PROMPT")

log "requesting report from Dayo..."
RESP=$(curl -s --max-time 300 -X POST "$GOCLAW_URL/v1/chat/completions" \
  -H "Authorization: Bearer $TOKEN" -H "X-GoClaw-User-Id: $USER_ID" \
  -H "X-GoClaw-Agent-Id: $AGENT" -H "Content-Type: application/json" -d "$REQ")

REPORT=$(printf '%s' "$RESP" | python -c 'import json,sys
try:
    d=json.load(sys.stdin); print(d["choices"][0]["message"]["content"].strip())
except Exception as e: print("")' )

if [ -z "$REPORT" ]; then
  log "FATAL: empty report. raw: $(printf '%s' "$RESP" | head -c 300)"; exit 1
fi
case "$REPORT" in
  REPORT_ERROR:*) log "ABORT: Dayo reported source error -> $REPORT"; exit 2;;
esac
log "report ready (${#REPORT} chars)"

# --- 3. delivery: send via andy (openclaw / zalo_listening) ---------------
# pass the report over stdin to avoid arg-length / quoting issues
SEND_RESULT=$(printf '%s' "$REPORT" | ssh -o BatchMode=yes -o ConnectTimeout=20 "$SSH_TARGET" \
  "MSG=\$(cat); '$OPENCLAW_CLI' message send --channel zalouser --account '$ZALO_ACCOUNT' --target '$ZALO_TARGET' --message \"\$MSG\" --json" 2>&1)

OK=$(printf '%s' "$SEND_RESULT" | python -c 'import json,sys
try:
    d=json.load(sys.stdin); r=d.get("payload",{}).get("result",{}); print("OK" if r.get("ok") else "FAIL:"+str(r.get("error")))
except Exception: print("FAIL:unparseable")' )

case "$OK" in
  OK) log "SENT to Zalo DM ($ZALO_TARGET) via $ZALO_ACCOUNT"; exit 0;;
  *)  log "FATAL: send failed -> $OK | raw: $(printf '%s' "$SEND_RESULT" | head -c 300)"; exit 3;;
esac
