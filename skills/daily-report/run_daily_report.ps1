# run_daily_report.ps1 — daily report host-side job (Windows Task Scheduler, Mon-Fri).
#
# MODES
#   -CollectOnly  (current production mode, task GoClaw-DailyReport-Gen at 17:00)
#       Ensure Docker is up -> run the HOST collector -> sync scripts into the container. The
#       GENERATE step is then triggered by the GoClaw cron `daily-report` (agent zip-crazy) at
#       17:10, so the report is produced in exactly one place.
#       This half cannot move into GoClaw: the collector reads D:\Projects\work git repos and the
#       Antigravity SQLite DB, and neither is mounted into the container (verified).
#   -IfMissing    (task GoClaw-DailyReport-Safety at 17:25) Safety net: generate ONLY if the cron
#       produced nothing for today. Moving GENERATE into GoClaw made the report depend on the
#       gateway being alive at 17:10 with a working agent turn; this restores a fallback without
#       reintroducing the duplicate run (it exits as soon as it sees today's posted draft).
#   (no flag)     Full pipeline (collector + generate + Friday weekly), for manual runs.
#
# Replaces the dead GoClaw cron. Self-heals the common failure mode:
#   - Docker engine down at fire time (the docker-desktop WSL distro can stop) -> start Docker
#     Desktop + wait for the engine before doing anything.
#   - LLM (Gemini ag-pro via GoClaw gateway) down -> retry once; last resort post a deterministic
#     fallback report to REVIEW only (Invoke-Generate $false — always produces a report).
#
# Pipeline additions (2026-07-18):
#   - HOST collector (git + Antigravity) runs BEFORE the container generate, writing
#     %USERPROFILE%\.claude\host-digest\*.json (readable in-container via /app/.claude-host ro mount).
#   - Friday: weekly report merged into this run (batch TEXT review, one DUYỆT publishes both).
#     Gate: set GOCLAW_WEEKLY=off (user env var) to disable the Friday weekly branch without
#     reverting this file.
#
# Robustness: native commands (docker/bash) returning non-zero must NOT abort the script silently
# (the old `$ErrorActionPreference=Stop` + PS7 native-error behavior killed it right after the
# "start" log line, producing empty failures). We log every outcome instead.
param([switch]$CollectOnly, [switch]$IfMissing)

$ErrorActionPreference = "Continue"
$PSNativeCommandUseErrorActionPreference = $false  # non-zero exit codes don't throw; we check $LASTEXITCODE
$PYTHON  = "C:\Program Files\Python313\python.exe"
$DOCKER  = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
$DDEXE   = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
$REPO    = "D:\Projects\personal\goclaw"
$GOCLAW  = "goclaw-goclaw-1"
$RUN     = "/app/workspace/_daily-report/daily_report_run.py"
$WEEKLY  = "/app/workspace/_daily-report/weekly_report.py"
$LOG     = Join-Path $REPO "skills\daily-report\_daily-report.log"
$DIGEST_DIR = Join-Path $env:USERPROFILE ".claude\host-digest"

function Log($m) {
  $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m
  try { Add-Content -Path $LOG -Value $line } catch {}
  # Write-Host, NOT Write-Output: Log is called from inside functions whose return value is an exit
  # code. Writing to the output stream made Invoke-Generate return Object[] (log lines + code), so
  # `$rc -eq 3` never matched (the LLM retry/fallback branch was dead) and `exit $rc` was garbage.
  Write-Host $line
}

function Test-Engine {
  & $DOCKER ps --format '{{.Names}}' *> $null
  return ($LASTEXITCODE -eq 0)
}

function Ensure-Docker {
  if (Test-Engine) { return $true }
  Log "docker engine DOWN -> starting Docker Desktop"
  & $DOCKER desktop start *> $null
  if ($LASTEXITCODE -ne 0 -and (Test-Path $DDEXE)) { Start-Process -FilePath $DDEXE -WindowStyle Hidden }
  for ($i = 0; $i -lt 36; $i++) {   # wait up to ~3 min for the engine
    Start-Sleep -Seconds 5
    if (Test-Engine) { Log "docker engine UP after ~$(($i+1)*5)s"; return $true }
  }
  Log "FATAL: docker engine still down after wait"
  return $false
}

function Wait-Healthy {
  for ($i = 0; $i -lt 24; $i++) {
    $st = (& $DOCKER inspect -f '{{.State.Health.Status}}' $GOCLAW 2>$null)
    if ($st -eq "healthy") { return $true }
    Start-Sleep -Seconds 5
  }
  return $false
}

function Sync-Scripts {
  # ONE exec, not one per file: 15 sequential `docker exec` cost ~4.8s of the run (measured) and
  # each round-trip is pure Docker Desktop overhead. The glob copies the same set of artefacts.
  & $DOCKER exec -u goclaw $GOCLAW sh -c 'cp -f /app/data/skills/daily-report/*.py /app/data/skills/daily-report/*.html /app/data/skills/daily-report/*.mjs /app/data/skills/daily-report/task_aliases.json /app/workspace/_daily-report/' 2>$null
  if ($LASTEXITCODE -ne 0) { Log "WARN: script sync rc=$LASTEXITCODE (container dung ban cu)" }
  # Refresh the gateway-token file so Zip-triggered publish/edit can auth (GoClaw v3.14 exec env
  # does NOT expose GOCLAW_GATEWAY_TOKEN; scripts read this chmod-600 file as fallback).
  & $DOCKER exec -u goclaw $GOCLAW sh -c 'printf "%s" "$GOCLAW_GATEWAY_TOKEN" > /app/workspace/_daily-report/.gwtoken && chmod 600 /app/workspace/_daily-report/.gwtoken' 2>$null
}

function Invoke-Collector([string[]]$WindowArgs, [string]$OutFile) {
  # HOST collector: git commits (D:\Projects\work) + Antigravity sessions -> sanitized JSON in
  # ~\.claude\host-digest\ (container reads it via the existing ro mount). Failure = WARN only;
  # the container pipeline degrades to Claude-sessions-only (freshness gate discards stale files).
  $env:PYTHONUTF8 = "1"
  & $PYTHON (Join-Path $REPO "skills\daily-report\collect_host_digest.py") @WindowArgs --out $OutFile 2>&1 |
    ForEach-Object { Log "collect> $_" }
  if ($LASTEXITCODE -ne 0) { Log "WARN: host collector rc=$LASTEXITCODE (report se chi dung Claude sessions)" }
}

function Invoke-Generate([bool]$RequireLlm, [bool]$NoPost = $false) {
  $dargs = @("exec","-u","goclaw",$GOCLAW,"python3",$RUN,"--hours","24")
  if ($RequireLlm) { $dargs += "--require-llm" }
  if ($NoPost)     { $dargs += "--no-post" }
  & $DOCKER @dargs 2>&1 | ForEach-Object { Log "gen> $_" }
  $code = $LASTEXITCODE          # capture before anything else can clobber it
  return $code
}

Log ("=== daily-report {0} start ===" -f $(if ($CollectOnly) { "collect" } else { "generate" }))
$rc = 1
try {
  if (-not (Ensure-Docker)) { Log "=== ABORTED: docker unavailable ==="; exit 1 }
  if (-not (Wait-Healthy))  { Log "WARN: goclaw container not healthy yet, trying anyway" }

  $isFriday   = ((Get-Date).DayOfWeek -eq 'Friday')
  $weeklyOn   = ($isFriday -and ($env:GOCLAW_WEEKLY -ne 'off'))

  # --- HOST collector (before Sync so the container reads fresh data) ---
  if ($weeklyOn) {
    # One scan for the full week window; the container derives the 24h daily slice from it.
    $monday = (Get-Date).Date.AddDays(1 - [int](Get-Date).DayOfWeek)
    Invoke-Collector @("--from", $monday.ToString("yyyy-MM-ddT00:00:00+07:00"),
                       "--to", (Get-Date -Format "yyyy-MM-ddTHH:mm:ss+07:00")) (Join-Path $DIGEST_DIR "week.json")
    Copy-Item (Join-Path $DIGEST_DIR "week.json") (Join-Path $DIGEST_DIR "latest.json") -Force -ErrorAction SilentlyContinue
  } else {
    Invoke-Collector @("--hours", "24") (Join-Path $DIGEST_DIR "latest.json")
  }

  Sync-Scripts

  if ($CollectOnly) {
    # Fresh digest + scripts are in place; the GoClaw cron runs the generate at 17:10.
    Log "=== collect OK (generate se do cron GoClaw chay) ==="
    exit 0
  }

  if ($IfMissing) {
    $today = (Get-Date -Format "yyyy-MM-dd")
    $state = & $DOCKER exec -u goclaw $GOCLAW sh -c "cat /app/workspace/_daily-report/active.json 2>/dev/null" 2>$null
    if ($state -match '"report_date":\s*"' + $today + '"' -and $state -match '"posted":\s*true') {
      Log "=== skip: cron GoClaw da tao bao cao hom nay ==="
      exit 0
    }
    Log "WARN: chua co bao cao cho $today (cron GoClaw khong chay?) -> tu generate"
  }

  # --- daily generate ---
  # LLM down (exit 3): retry once (Gemini via gateway — transient), then post the deterministic
  # fallback report to REVIEW only (always produces a report; NEVER dropped — agent-r1-f02).
  $rc = Invoke-Generate $true $weeklyOn
  if ($rc -eq 3) {
    Log "LLM down (exit 3) -> retry once"
    $rc = Invoke-Generate $true $weeklyOn
    if ($rc -eq 3) {
      Log "WARNING: LLM still down after retry -> posting fallback report to REVIEW only"
      $rc = Invoke-Generate $false $weeklyOn
    }
  }

  # --- Friday: weekly report (independent of daily rc; failure never blocks daily) ---
  if ($weeklyOn) {
    Log "--- friday weekly report ---"
    & $DOCKER exec -u goclaw $GOCLAW python3 $WEEKLY --report 2>&1 | ForEach-Object { Log "weekly> $_" }
    if ($LASTEXITCODE -ne 0) { Log "WARN: weekly report FAILED rc=$LASTEXITCODE (daily khong anh huong)" }
    # Batch post: publish both TEXT reviews together (one visible batch, one DUYET).
    & $DOCKER exec -u goclaw $GOCLAW python3 $RUN --post-pending 2>&1 | ForEach-Object { Log "post> $_" }
    if ($LASTEXITCODE -ne 0) { Log "WARN: batch post rc=$LASTEXITCODE" }
  }
} catch {
  Log "EXCEPTION: $($_.Exception.Message)"
  $rc = 1
}

if ($rc -eq 0) { Log "=== generate OK ===" } else { Log "=== generate FAILED rc=$rc (see log) ===" }
exit $rc
