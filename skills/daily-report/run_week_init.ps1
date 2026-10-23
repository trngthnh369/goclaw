# run_week_init.ps1 — Monday 08:30 wrapper (Windows Task GoClaw-WeekSheetInit): create the new
# week's tab in the "AI Agent" sheet + carry over unfinished tasks. Sheet-only — week_init.py
# never calls the GoClaw gateway, so this wrapper does NOT provision .gwtoken (least privilege).
$ErrorActionPreference = "Continue"
$PSNativeCommandUseErrorActionPreference = $false
$DOCKER  = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
$DDEXE   = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
$REPO    = "D:\Projects\personal\goclaw"
$GOCLAW  = "goclaw-goclaw-1"
$LOG     = Join-Path $REPO "skills\daily-report\_week-init.log"

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
  for ($i = 0; $i -lt 36; $i++) {
    Start-Sleep -Seconds 5
    if (Test-Engine) { Log "docker engine UP after ~$(($i+1)*5)s"; return $true }
  }
  Log "FATAL: docker engine still down after wait"
  return $false
}

Log "=== week-init start ==="
$rc = 1
try {
  if (-not (Ensure-Docker)) { Log "=== week-init ABORTED: docker unavailable ==="; exit 1 }

  # Sync the full transitive dependency set (week_init -> daily_report_sheet -> sheets_client).
  foreach ($f in @("week_init.py","daily_report_sheet.py","sheets_client.py")) {
    & $DOCKER exec -u goclaw $GOCLAW cp -f "/app/data/skills/daily-report/$f" "/app/workspace/_daily-report/$f" 2>$null
  }
  # Import smoke-test before the real run (catches a missing-dep sync gap loudly).
  & $DOCKER exec -u goclaw -w /app/workspace/_daily-report $GOCLAW python3 -c "import week_init" 2>&1 |
    ForEach-Object { Log "smoke> $_" }
  if ($LASTEXITCODE -ne 0) { Log "=== week-init ABORTED: import smoke failed ==="; exit 1 }

  & $DOCKER exec -u goclaw $GOCLAW python3 /app/workspace/_daily-report/week_init.py 2>&1 |
    ForEach-Object { Log "init> $_" }
  $rc = $LASTEXITCODE
} catch {
  Log "EXCEPTION: $($_.Exception.Message)"
  $rc = 1
}

if ($rc -eq 0) { Log "=== week-init OK ===" } else { Log "=== week-init FAILED rc=$rc ===" }
exit $rc
