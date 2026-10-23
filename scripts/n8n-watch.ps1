# n8n-watch.ps1 - Canh bao execution LOI moi tren n8n production (pain point: cron fail am tham).
#
# Chay tren HOST (khong phai trong container): n8nctl da co credential trong Windows keyring
# (profile pcvn-prod) -> API key KHONG bao gio phai di vao container GoClaw. Cung nguyen tac
# "status-push inversion" da ap cho SSH: giu secret o noi da co, day KET QUA di.
#
# Dang ky:
#   $ps = (Get-Command pwsh).Source
#   schtasks /create /tn "GoClaw-N8N-Watch" /tr "`"$ps`" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File D:\Projects\personal\goclaw\scripts\n8n-watch.ps1" /sc minute /mo 60 /f
#
# Lan chay DAU TIEN chi ghi nhan moc hien tai, KHONG alert (tranh spam toan bo loi cu).

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $Root
$EnvFile = Join-Path $RepoRoot 'cliproxy\probe.env'
$StateFile = Join-Path $RepoRoot 'cliproxy\n8n-watch-state.json'
$LogFile = Join-Path $RepoRoot 'cliproxy\_n8n-watch.log'
$Limit = 50

function Write-Log([string]$m) {
  Add-Content -LiteralPath $LogFile -Value ("[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m)
}

if (-not (Test-Path $EnvFile)) { exit 0 }
$webhook = $null
foreach ($line in Get-Content $EnvFile) {
  if ($line -match '^\s*DISCORD_WEBHOOK\s*=\s*(.+)$') { $webhook = $Matches[1].Trim() }
}
if (-not $webhook) { exit 0 }

$state = @{ lastMaxId = 0; lastAlertAt = $null }
if (Test-Path $StateFile) {
  try {
    $j = Get-Content $StateFile -Raw | ConvertFrom-Json
    if ($null -ne $j.lastMaxId) { $state.lastMaxId = [int]$j.lastMaxId }
    if ($j.lastAlertAt) { $state.lastAlertAt = $j.lastAlertAt }
  } catch {}
}

# --- Lay executions loi ---
try {
  $raw = & n8nctl execution list --status error --limit $Limit 2>&1 | Out-String
  $execs = $raw | ConvertFrom-Json
} catch {
  # n8n khong tra loi = SU KIEN DANG BAO, khong duoc coi la "0 loi" (bai hoc plan-review agent-r1-f11).
  $msg = "[N8N-WATCH] Khong query duoc n8n (n8nctl loi hoac instance down) - KHONG PHAI 'khong co loi'. Kiem tra n8n."
  try { Invoke-RestMethod -Uri $webhook -Method Post -ContentType 'application/json' -TimeoutSec 20 -Body (@{content=$msg} | ConvertTo-Json) | Out-Null } catch {}
  Write-Log "query failed: $($_.Exception.Message)"
  exit 1
}

if (-not $execs) { Write-Log "no error executions"; exit 0 }
if ($execs -isnot [array]) { $execs = @($execs) }

$maxId = ($execs | ForEach-Object { [int]$_.id } | Measure-Object -Maximum).Maximum

# Lan dau: chi ghi moc, khong alert.
if ($state.lastMaxId -eq 0) {
  $state.lastMaxId = $maxId
  ($state | ConvertTo-Json) | Set-Content $StateFile -Encoding utf8
  Write-Log "baseline set at execId=$maxId (no alert on first run)"
  exit 0
}

$new = @($execs | Where-Object { [int]$_.id -gt $state.lastMaxId })
if ($new.Count -eq 0) { Write-Log "no new errors (max=$maxId)"; exit 0 }

# --- Map workflowId -> ten, de alert doc duoc ---
$names = @{}
try {
  $wfRaw = & n8nctl workflow list --limit 200 2>&1 | Out-String
  foreach ($w in ($wfRaw | ConvertFrom-Json)) { $names[$w.id] = $w.name }
} catch { Write-Log "workflow name lookup failed (alert se dung ID)" }

$lines = $new | Sort-Object { [int]$_.id } -Descending | Select-Object -First 10 | ForEach-Object {
  $wf = if ($names[$_.workflowId]) { $names[$_.workflowId] } else { $_.workflowId }
  $when = try { ([datetime]$_.startedAt).ToLocalTime().ToString('MM-dd HH:mm') } catch { $_.startedAt }
  "- $wf (exec $($_.id), $when)"
}

$body = "[N8N-WATCH] $($new.Count) execution LOI moi tren n8n production:`n" + ($lines -join "`n")
if ($new.Count -gt 10) { $body += "`n... va $($new.Count - 10) loi khac" }

try {
  # -TimeoutSec bat buoc: POST treo thi task chay den khi bi ExecutionTimeLimit giet, state khong
  # duoc ghi va khong co alert nao - nhin vao khong phan biet duoc voi "khong co loi moi".
  Invoke-RestMethod -Uri $webhook -Method Post -ContentType 'application/json' -TimeoutSec 20 -Body (@{content=$body} | ConvertTo-Json) | Out-Null
  $state.lastMaxId = $maxId
  $state.lastAlertAt = (Get-Date).ToString('o')
  Write-Log "alerted $($new.Count) new errors, advance lastMaxId=$maxId"
} catch {
  # KHONG advance lastMaxId khi gui that bai -> lan sau bao lai (khong nuot canh bao).
  Write-Log "discord post failed: $($_.Exception.Message) - giu nguyen lastMaxId de retry"
}

($state | ConvertTo-Json) | Set-Content $StateFile -Encoding utf8
