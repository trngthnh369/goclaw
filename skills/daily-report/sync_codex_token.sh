#!/usr/bin/env bash
# sync_codex_token.sh — mirror the Codex CLI's OAuth token into GoClaw (no phone-gate, no OAuth flow).
#
# Why: GoClaw's openai-codex provider and the local Codex CLI share ONE ChatGPT OAuth identity.
# When the CLI refreshes its token, OpenAI rotates the credentials and GoClaw's stored copy gets
# invalidated (the ~5-day "Codex died" symptom). The official re-auth flow now also demands an SMS
# code. This script bypasses all of that: it reads the CLI's CURRENT auth (~/.codex/auth.json, which
# the CLI keeps valid via your Google/ChatGPT login) and writes it straight into GoClaw's encrypted
# store. Run it whenever the daily report stops (LLM 401/token_invalidated), or schedule it daily
# just before 17:10 so GoClaw always has a fresh token.
#
# Usage:  bash skills/daily-report/sync_codex_token.sh
set -uo pipefail

GOCLAW="${GOCLAW_CONTAINER:-goclaw-goclaw-1}"
PG="${PG_CONTAINER:-goclaw-postgres-1}"
TENANT="${GOCLAW_TENANT:-0193a5b0-7000-7000-8000-000000000001}"
ENCRYPT_PY="/app/data/skills/daily-report/codex_token_encrypt.py"

echo "[sync-codex] encrypting CLI token (in $GOCLAW)..."
# codex_token_encrypt.py self-adds pylib to sys.path, so no PYTHONPATH env needed.
VALS=$(docker exec -u goclaw -i "$GOCLAW" sh -c "python3 $ENCRYPT_PY" 2>/dev/null)
ENC_ACCESS=$(printf '%s\n' "$VALS" | grep '^ENC_ACCESS=' | cut -d= -f2-)
ENC_REFRESH=$(printf '%s\n' "$VALS" | grep '^ENC_REFRESH=' | cut -d= -f2-)
ACCOUNT_ID=$(printf '%s\n' "$VALS" | grep '^ACCOUNT_ID=' | cut -d= -f2-)

if [ -z "$ENC_ACCESS" ] || [ -z "$ENC_REFRESH" ]; then
  echo "[sync-codex] FATAL: could not read/encrypt CLI token. Is ~/.codex/auth.json present + logged in?" >&2
  exit 1
fi

echo "[sync-codex] writing provider + refresh secret to DB..."
docker exec -i "$PG" psql -U goclaw -d goclaw >/dev/null 2>&1 <<SQL
DELETE FROM config_secrets WHERE key='oauth.openai-codex.refresh_token' AND tenant_id='$TENANT';
DELETE FROM llm_providers WHERE name='openai-codex';
INSERT INTO llm_providers (name, display_name, provider_type, api_base, api_key, enabled, settings, tenant_id)
VALUES ('openai-codex','Codex','chatgpt_oauth','','$ENC_ACCESS', true, '{"account_id":"$ACCOUNT_ID"}'::jsonb, '$TENANT');
INSERT INTO config_secrets (key, value, tenant_id)
VALUES ('oauth.openai-codex.refresh_token', convert_to('$ENC_REFRESH','UTF8'), '$TENANT');
SQL

echo "[sync-codex] restarting GoClaw to reload provider..."
docker restart "$GOCLAW" >/dev/null 2>&1
until [ "$(docker inspect -f '{{.State.Health.Status}}' "$GOCLAW" 2>/dev/null)" = "healthy" ]; do sleep 3; done
echo "[sync-codex] DONE — GoClaw now uses the Codex CLI token. (account=$ACCOUNT_ID)"
