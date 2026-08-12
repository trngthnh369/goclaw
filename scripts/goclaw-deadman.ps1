# goclaw-deadman.ps1 - Dead-man's-switch cho heartbeat + daily-ops-digest (plan PA P0.2, agy-r2-f03)
# Companion cua cliproxy/probe-antigravity.ps1 (KHONG sua probe - script rieng, dung chung probe.env).
# ASCII-only: chay duoi pwsh/powershell scheduled task - khong emoji/unicode trong string.
#
# Dang ky:
#   $ps = (Get-Command pwsh).Source
#   schtasks /create /tn "GoClaw-Deadman" /tr "`"$ps`" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File D:\Projects\personal\goclaw\scripts\goclaw-deadman.ps1" /sc minute /mo 30 /f
#
# False-positive guards (verified plan-review agy-r2-f03): heartbeat CHI assert 09:00-23:00 ICT
# (active_hours 07-23 + 2h margin); digest CHI assert sau 06:30 ICT voi cua so rolling 26h;
# chua deploy (0 row config) -> skip im lang.

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $Root
$EnvFile = Join-Path $RepoRoot 'cliproxy\probe.env'      # secret o thu muc gitignored
$StateFile = Join-Path $RepoRoot 'cliproxy\deadman-state.json'
$ReAlertHours = 6

if (-not (Test-Path $EnvFile)) { exit 0 }  # chua cau hinh - chua active
$webhook = $null
foreach ($line in Get-Content $EnvFile) {
  if ($line -match '^\s*DISCORD_WEBHOOK\s*=\s*(.+)$') { $webhook = $Matches[1].Trim() }
}
if (-not $webhook) { exit 0 }

$state = @{}
if (Test-Path $StateFile) {
  try { (Get-Content $StateFile -Raw | ConvertFrom-Json).psobject.Properties | ForEach-Object { $state[$_.Name] = $_.Value } } catch {}
}

function Send-Alert([string]$key, [string]$msg) {
  $now = Get-Date
  $last = $null
  if ($state[$key]) { try { $last = [datetime]$state[$key] } catch {} }
  if ($last -and ($now - $last).TotalHours -lt $script:ReAlertHours) { return }
  try {
    Invoke-RestMethod -Uri $script:webhook -Method Post -ContentType 'application/json' `
      -Body (@{content = "[DEADMAN] $msg"} | ConvertTo-Json) | Out-Null
    $state[$key] = $now.ToString('o')
  } catch {}
}

function Invoke-Psql([string]$sql) {
  $out = docker exec goclaw-postgres-1 psql -U goclaw -d goclaw -Atc $sql 2>$null
  if ($LASTEXITCODE -ne 0) { throw "psql failed" }
  return $out
}

$ict = [System.TimeZoneInfo]::ConvertTimeBySystemTimeZoneId((Get-Date).ToUniversalTime(), 'SE Asia Standard Time')
$hm = $ict.Hour * 60 + $ict.Minute

try {
  # Check A - heartbeat overdue/error. CHI trong 09:00-23:00 ICT. 0 heartbeat enabled -> skip tu nhien.
  if ($hm -ge 540 -and $hm -le 1380) {
    $rows = Invoke-Psql "SELECT a.agent_key || ':' || COALESCE(h.last_status,'never') FROM agent_heartbeats h JOIN agents a ON a.id = h.agent_id WHERE h.enabled AND (now() > h.next_run_at + (h.interval_sec * interval '1 second') OR h.last_status = 'error')"
    if ($rows) { Send-Alert 'heartbeat' "GoClaw heartbeat overdue/error: $($rows -join ', ') - ticker chet, agent stuck, hoac provider loi (stack fail-silent, khong tu bao)." }
  }

  # Check B - daily-ops-digest vang mat. CHI sau 06:30 ICT; rolling 26h; cron chua ton tai/disabled -> skip.
  if ($hm -ge 390) {
    $cfg = Invoke-Psql "SELECT id FROM cron_jobs WHERE name = 'daily-ops-digest' AND enabled LIMIT 1"
    if ($cfg) {
      $ok = Invoke-Psql "SELECT count(*) FROM cron_run_logs WHERE job_id = '$cfg' AND error IS NULL AND ran_at > now() - interval '26 hours'"
      if ([int]$ok -eq 0) { Send-Alert 'digest' "Daily Ops Digest KHONG chay thanh cong trong 26h (cron fail-silent - kiem tra gateway/provider/cron_run_logs)." }
    }
  }

  if ($state['db-unreachable']) { $state.Remove('db-unreachable') }  # recovery - reset de lan sau alert lai
} catch {
  # Check C - DB/docker unreachable: chinh deadman cung phai len tieng.
  Send-Alert 'db-unreachable' "deadman khong query duoc goclaw-postgres-1 (docker/pg down?) - moi giam sat in-band dang mu."
}

($state | ConvertTo-Json) | Set-Content $StateFile -Encoding utf8
