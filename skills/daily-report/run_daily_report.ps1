# run_daily_report.ps1 — daily report GENERATE wrapper (Windows Task Scheduler, 17:10 Mon-Fri).
#
# Replaces the dead GoClaw cron (id 019e973c, Zip->Codex). Self-heals the Codex provider:
#   1. generate with --require-llm (refuses to post a rule-violating fallback report)
#   2. if the LLM is down (exit 3), sync the Codex CLI token into GoClaw (+restart) and retry
#   3. last resort: generate WITHOUT the guard so a (fallback) report still reaches REVIEW —
#      the Discord review gate stops it from auto-reaching TEAM AI, so a manual check still applies
#
# The bridge/gemini-cli path is GONE (Google deprecated the free-tier client). LLM = Codex via GoClaw.
# No DAILY_REPORT_LLM_URL is set, so daily_report_run.py uses the GoClaw gateway (agent:zip-crazy).
$ErrorActionPreference = "Stop"
$PYTHON = "C:\Program Files\Python313\python.exe"
$BASH   = "C:\Program Files\Git\bin\bash.exe"
$REPO   = "D:\Projects\personal\goclaw"
$GOCLAW = "goclaw-goclaw-1"
$RUN    = "/app/workspace/_daily-report/daily_report_run.py"
$LOG    = Join-Path $REPO "skills\daily-report\_daily-report.log"

function Log($m) {
  $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m
  Add-Content -Path $LOG -Value $line
  Write-Output $line
}

function Sync-Scripts {
  # keep the runtime copy current with the repo (source of truth is ./skills/daily-report)
  $files = @("daily_report_run.py","daily_report_publish.py","daily_report_sheet.py",
             "daily_report_edit.py","build_and_render.py","digest_sessions.py","sheets_client.py",
             "render_report.mjs","template.html","task_aliases.json")
  foreach ($f in $files) {
    docker exec -u goclaw $GOCLAW cp -f "/app/data/skills/daily-report/$f" "/app/workspace/_daily-report/$f" 2>$null
  }
}

function Invoke-Generate([bool]$RequireLlm) {
  # NB: avoid the automatic $args variable name here.
  $dargs = @("exec","-u","goclaw",$GOCLAW,"python3",$RUN,"--hours","24")
  if ($RequireLlm) { $dargs += "--require-llm" }
  & docker @dargs 2>&1 | ForEach-Object { Log "gen> $_" }
  return $LASTEXITCODE
}

Log "=== daily-report generate start ==="
Sync-Scripts

$rc = Invoke-Generate $true
if ($rc -eq 3) {
  Log "LLM down (exit 3) -> syncing Codex token + restart"
  & $BASH (Join-Path $REPO "skills/daily-report/sync_codex_token.sh") 2>&1 | ForEach-Object { Log "sync> $_" }
  $rc = Invoke-Generate $true
  if ($rc -eq 3) {
    Log "WARNING: LLM still down after token sync -> posting fallback report to REVIEW only"
    $rc = Invoke-Generate $false
  }
}

if ($rc -eq 0) { Log "=== generate OK ===" }
else { Log "=== generate FAILED rc=$rc (see log) ===" }
exit $rc
