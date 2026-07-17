# run_daily_report.ps1 — daily report GENERATE wrapper (Windows Task Scheduler, 17:10 Mon-Fri).
#
# Replaces the dead GoClaw cron. Self-heals BOTH common failure modes:
#   - Docker engine down at fire time (the docker-desktop WSL distro can stop) -> start Docker
#     Desktop + wait for the engine before doing anything.
#   - Codex token death (~5-day cycle) -> generate with --require-llm; on exit 3 sync the Codex CLI
#     token into GoClaw (+restart) and retry; last resort post a fallback report to REVIEW only.
#
# Robustness: native commands (docker/bash) returning non-zero must NOT abort the script silently
# (the old `$ErrorActionPreference=Stop` + PS7 native-error behavior killed it right after the
# "start" log line, producing empty failures). We log every outcome instead.
#
# LLM = Codex via GoClaw (no DAILY_REPORT_LLM_URL; gemini-cli/agy bridge is dead).
$ErrorActionPreference = "Continue"
$PSNativeCommandUseErrorActionPreference = $false  # non-zero exit codes don't throw; we check $LASTEXITCODE
$PYTHON  = "C:\Program Files\Python313\python.exe"
$BASH    = "C:\Program Files\Git\bin\bash.exe"
$DOCKER  = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
$DDEXE   = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
$REPO    = "D:\Projects\personal\goclaw"
$GOCLAW  = "goclaw-goclaw-1"
$RUN     = "/app/workspace/_daily-report/daily_report_run.py"
$LOG     = Join-Path $REPO "skills\daily-report\_daily-report.log"

function Log($m) {
  $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m
  try { Add-Content -Path $LOG -Value $line } catch {}
  Write-Output $line
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
  $files = @("daily_report_run.py","daily_report_publish.py","daily_report_sheet.py",
             "daily_report_edit.py","build_and_render.py","digest_sessions.py","sheets_client.py",
             "weekly_report.py","build_summary_tab.py","render_report.mjs","template.html","task_aliases.json")
  foreach ($f in $files) {
    & $DOCKER exec -u goclaw $GOCLAW cp -f "/app/data/skills/daily-report/$f" "/app/workspace/_daily-report/$f" 2>$null
  }
  # Refresh the gateway-token file so Zip-triggered publish/edit can auth (GoClaw v3.14 exec env
  # does NOT expose GOCLAW_GATEWAY_TOKEN; scripts read this chmod-600 file as fallback).
  & $DOCKER exec -u goclaw $GOCLAW sh -c 'printf "%s" "$GOCLAW_GATEWAY_TOKEN" > /app/workspace/_daily-report/.gwtoken && chmod 600 /app/workspace/_daily-report/.gwtoken' 2>$null
}

function Invoke-Generate([bool]$RequireLlm) {
  $dargs = @("exec","-u","goclaw",$GOCLAW,"python3",$RUN,"--hours","24")
  if ($RequireLlm) { $dargs += "--require-llm" }
  & $DOCKER @dargs 2>&1 | ForEach-Object { Log "gen> $_" }
  return $LASTEXITCODE
}

Log "=== daily-report generate start ==="
$rc = 1
try {
  if (-not (Ensure-Docker)) { Log "=== generate ABORTED: docker unavailable ==="; exit 1 }
  if (-not (Wait-Healthy))  { Log "WARN: goclaw container not healthy yet, trying anyway" }

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
} catch {
  Log "EXCEPTION: $($_.Exception.Message)"
  $rc = 1
}

if ($rc -eq 0) { Log "=== generate OK ===" } else { Log "=== generate FAILED rc=$rc (see log) ===" }
exit $rc
