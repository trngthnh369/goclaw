#!/usr/bin/env bash
# Verify a ContentFactory canary run (E2E-1 / E2E-2) against the live gateway.
#
# Read-only. Never triggers a run and never publishes: point it at a session key
# produced by POST /v1/agents/cf-director/wake.
#
# Usage:
#   scripts/contentfactory-e2e-verify.sh <session-key-prefix> [since-timestamp]
#
# Example:
#   scripts/contentfactory-e2e-verify.sh e2e-cf-20260806-neg '2026-08-06 00:00'
#
# Env:
#   PG_CONTAINER   postgres container name        (default goclaw-postgres-1)
#   PG_USER/PG_DB  database credentials           (default goclaw/goclaw)
#   GW_CONTAINER   gateway container name         (default goclaw-goclaw-1)
#   REVIEW_TARGET  Discord review channel id      (default 1530127001602625677)

set -uo pipefail

SESSION_PREFIX="${1:?usage: $0 <session-key-prefix> [since-timestamp]}"
SINCE="${2:-$(date -u -d '2 hours ago' '+%Y-%m-%d %H:%M' 2>/dev/null || echo '2026-01-01 00:00')}"

PG_CONTAINER="${PG_CONTAINER:-goclaw-postgres-1}"
PG_USER="${PG_USER:-goclaw}"
PG_DB="${PG_DB:-goclaw}"
GW_CONTAINER="${GW_CONTAINER:-goclaw-goclaw-1}"
REVIEW_TARGET="${REVIEW_TARGET:-1530127001602625677}"

q() { docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -tAc "$1" 2>/dev/null | tr -d '\r'; }

fails=0
pass() { printf '  PASS  %s\n' "$1"; }
fail() { printf '  FAIL  %s\n' "$1"; fails=$((fails + 1)); }
info() { printf '  ..    %s\n' "$1"; }

echo "ContentFactory canary verification"
echo "  session prefix : $SESSION_PREFIX"
echo "  since          : $SINCE"
echo

# --- 1. Pipeline shape -------------------------------------------------------
echo "[1] Pipeline agents"
for agent in cf-researcher cf-writer cf-auditor; do
  n=$(q "select count(*) from sessions s join agents a on a.id=s.agent_id
         where a.agent_key='$agent' and s.created_at > '$SINCE';")
  [ "${n:-0}" -ge 1 ] && pass "$agent ran ($n session(s))" || fail "$agent did not run"
done

designer=$(q "select count(*) from sessions s join agents a on a.id=s.agent_id
              where a.agent_key='cf-designer' and s.created_at > '$SINCE';")
# Only the auditor's OWN output counts. The delegation prompt quotes the phrase
# "AUDIT_VERDICT: PASS" as an instruction, so matching the whole session blob
# reports a pass on every run, including ones the gate correctly blocked.
audit_passed=$(q "select count(*) from sessions s join agents a on a.id=s.agent_id,
                    jsonb_array_elements(s.messages) m
                  where a.agent_key='cf-auditor' and s.created_at > '$SINCE'
                    and m->>'role' = 'assistant'
                    and m->>'content' ~ '(^|\n)AUDIT_VERDICT: PASS';")
if [ "${audit_passed:-0}" -ge 1 ]; then
  [ "${designer:-0}" -eq 1 ] \
    && pass "cf-designer ran exactly once after audit PASS" \
    || fail "cf-designer ran ${designer:-0} time(s) after audit PASS — expected exactly 1"
else
  [ "${designer:-0}" -eq 0 ] \
    && pass "cf-designer did not run (no audit PASS) — gate held" \
    || fail "cf-designer ran ${designer:-0} time(s) without an audit PASS"
fi

# --- 2. Tool authorization (proves the P0.2 policy fix actually shipped) ------
# Counting sessions cannot distinguish a working policy fix from a reverted one.
echo
echo "[2] Tool authorization"
auditor_sessions=$(q "select count(*) from sessions s join agents a on a.id=s.agent_id
                      where a.agent_key='cf-auditor' and s.created_at > '$SINCE';")
if [ "${auditor_sessions:-0}" -eq 0 ]; then
  # Do not report PASS here: with no auditor session the check proves nothing,
  # and a vacuous PASS is exactly how a reverted policy fix slips through.
  fail "cf-auditor had no session in the window — tool-authorization check NOT evaluated"
else
  leaked=$(q "select count(*) from sessions s join agents a on a.id=s.agent_id
              where a.agent_key='cf-auditor' and s.created_at > '$SINCE'
                and (s.messages::text like '%\"name\":\"read_file\"%'
                  or s.messages::text like '%\"name\":\"write_file\"%'
                  or s.messages::text like '%\"name\":\"list_files\"%');")
  [ "${leaked:-0}" -eq 0 ] \
    && pass "cf-auditor made no read_file/write_file/list_files call (${auditor_sessions} session(s) checked)" \
    || fail "cf-auditor called a file tool in ${leaked} session(s) — deny-wins is NOT in the running binary"
fi

denies=$(q "select tools_config->'deny' from agents where agent_key='cf-director';")
case "$denies" in
  *team_tasks*) pass "cf-director denies team_tasks (audit-gate bypass closed)" ;;
  *)            fail "cf-director does NOT deny team_tasks — the dispatch path can bypass the audit gate" ;;
esac

zip=$(q "select l.status from agent_links l
         join agents s on s.id=l.source_agent_id
         join agents t on t.id=l.target_agent_id
         where s.agent_key='cf-director' and t.agent_key='zip-crazy';")
[ "$zip" = "revoked" ] && pass "cf-director -> zip-crazy revoked" || fail "cf-director -> zip-crazy is '$zip', expected revoked"

# --- 3. Facebook must stay untouched in a canary -----------------------------
echo
echo "[3] Facebook safety"
# Scope to the run window: container logs outlive restarts, so an unscoped grep
# reports every historical human-approved publish as a canary leak.
LOG_SINCE="$(printf '%s' "$SINCE" | tr ' ' 'T')Z"
gwlogs() { docker logs --since "$LOG_SINCE" "$GW_CONTAINER" 2>&1; }

posts=$(gwlogs | grep -c "message.feed_post_approval" || true)
[ "${posts:-0}" -eq 0 ] \
  && pass "no feed_post_approval since $SINCE" \
  || fail "${posts} feed_post_approval event(s) since $SINCE — a canary must never publish"

# Adapter lifecycle lines ("starting channel", "channel registered") name the
# channel too — only outbound traffic counts as a publish.
fbout=$(gwlogs | grep "channel=fb-page" \
        | grep -viE "starting channel|channel registered|channel stopped|channel ready" \
        | grep -c . || true)
[ "${fbout:-0}" -eq 0 ] \
  && pass "no fb-page outbound since $SINCE" \
  || fail "${fbout} fb-page outbound event(s) since $SINCE"

info "ledger entries under dataDir (expect unchanged):"
docker exec "$GW_CONTAINER" sh -c 'ls -1 "$GOCLAW_DATA_DIR/.goclaw/feed-post-ledger" 2>/dev/null | wc -l' 2>/dev/null \
  | sed 's/^/        /' || echo "        (ledger dir not readable)"

# --- 4. Terminal action ------------------------------------------------------
echo
echo "[4] Terminal action"
run_summary=$(q "select coalesce(summary,'') from cron_run_logs
                 where ran_at > '$SINCE' order by ran_at desc limit 1;")
if [ -z "$run_summary" ]; then
  info "no cron_run_logs row in window (wake runs do not write one)"
else
  case "$run_summary" in
    *"Đang "*|*"Sẽ tiếp tục"*|*"Đã nhận kết quả"*)
      fail "run ended on progress narration, not a terminal action: ${run_summary:0:90}" ;;
    *) pass "run summary is not progress narration" ;;
  esac
fi

# Direct signal from the runtime, independent of how the summary happens to be
# worded: the run finished with its required terminal action still pending.
# A run can also end this way with a summary that reads like a result, so this
# check is not redundant with the narration heuristic above.
noterm=$(gwlogs | grep -c "contentfactory.run_without_terminal_action" || true)
[ "${noterm:-0}" -eq 0 ] \
  && pass "no run ended with a pending terminal action" \
  || fail "${noterm} run(s) ended without the required terminal action (no review draft, no abort notice)"

# Nudges are recoveries, not failures — but a run that needed them is worth
# seeing, because the budget is 2 and a run that spends both is one step from failing.
nudges=$(gwlogs | grep 'debug.llm.retry_guard' | grep -c 'reason=terminal_action_pending' || true)
info "terminal-action nudges fired: ${nudges:-0} (budget 2 per run)"

# Only channel=wake matters here — that is the cross-target breadcrumb a /wake
# canary produces. channel=http warnings come from a different, unrelated path.
breadcrumb=$(gwlogs | grep "unknown channel for outbound message" | grep -c "channel=wake" || true)
[ "${breadcrumb:-0}" -eq 0 ] \
  && pass "no 'unknown channel=wake' breadcrumb warning" \
  || fail "${breadcrumb} 'unknown channel=wake' warning(s)"

# --- 5. Cost baseline for the P2.0 gate --------------------------------------
echo
echo "[5] Baseline (records the numbers the P2.0 decision needs)"
q "select '        '||a.agent_key||'  in='||coalesce(s.input_tokens,0)||'  out='||coalesce(s.output_tokens,0)
     ||'  msgs='||jsonb_array_length(s.messages)
     ||'  dur='||round(extract(epoch from (s.updated_at - s.created_at)))||'s'
   from sessions s join agents a on a.id=s.agent_id
   where s.created_at > '$SINCE' and a.agent_key like 'cf-%'
   order by s.created_at;"

echo
if [ "$fails" -eq 0 ]; then
  echo "RESULT: PASS"
else
  echo "RESULT: FAIL ($fails check(s))"
fi
exit "$fails"
