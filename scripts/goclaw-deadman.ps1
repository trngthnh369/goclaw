# goclaw-deadman.ps1 - Dead-man's-switch cho heartbeat + daily-ops-digest (plan PA P0.2, agy-r2-f03)
# Companion cua cliproxy/probe-antigravity.ps1 (KHONG sua probe - script rieng, dung chung probe.env).
# ASCII-only: chay duoi pwsh/powershell scheduled task - khong emoji/unicode trong string.
#
# Dang ky:
#   $ps = (Get-Command pwsh).Source
#   schtasks /create /tn "GoClaw-Deadman" /tr "`"$ps`" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File D:\Projects\personal\goclaw\scripts\goclaw-deadman.ps1" /sc minute /mo 30 /f
#
# False-positive guards (verified plan-review agy-r2-f03): heartbeat CHI assert 09:00-23:00 ICT
# (active_hours 07-23 + 2h margin); cron CHI alert 07:00-23:00 ICT (tracking van chay 24/24
# de dong ho 90p bat dau dung luc, khong doi toi sang moi tinh); chua co job -> skip im lang.

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

  # Check B - cron job im lang. Thay cho check 'daily-ops-digest' cu: job do khong ton tai
  # trong cron_jobs nen nhanh do la code chet, trong khi 6 job that dang chay khong ai canh.
  #
  # KHONG alert theo tung loi le: 100% loi 7 ngay qua la HTTP 429 model_cooldown (ag-pro) va
  # timeout cliproxy:8317 - transient, co success hai ben -> alert moi loi = spam den muc bi tat.
  # Tin hieu dung la VANG MAT SUCCESS trong cua so mong doi.
  #
  # B1 stale: period tu suy ra tu (next_run_at - last_run_at) thay vi hardcode cadence.
  # finishRun (pg/cron_scheduler.go:344) advance next_run_at BAT KE status, nen period van
  # dung ca khi job fail; job T2-T6 cuoi tuan tu ra period 72h ma khong can biet cron expr.
  # B2 stuck claim: claimDueJob (pg/cron_scheduler.go:364) set next_run_at=NULL de claim.
  # Gateway chet giua chung -> ket NULL vinh vien, job KHONG BAO GIO chay lai, khong log,
  # khong loi. now() > next_run_at + grace miss sach case nay vi so sanh voi NULL ra NULL.
  $stale = @(Invoke-Psql @"
SELECT j.name || ' (' || round(extract(epoch FROM now() - s.last_ok) / 3600) || 'h khong success)'
FROM cron_jobs j
JOIN LATERAL (SELECT max(ran_at) AS last_ok FROM cron_run_logs l WHERE l.job_id = j.id AND l.error IS NULL) s ON true
WHERE j.enabled AND j.schedule_kind IN ('cron','every')
  AND j.next_run_at IS NOT NULL AND j.last_run_at IS NOT NULL
  AND j.next_run_at > j.last_run_at AND s.last_ok IS NOT NULL
  AND now() - s.last_ok > (j.next_run_at - j.last_run_at) + interval '90 minutes'
"@ | Where-Object { $_ })

  # Tracking claim chay 24/24 (khong gate theo gio) - grace 90p = 3 chu ky poll, trong khi run
  # dai nhat tung do la 37p (max duration_ms 2219397, polymarket p95 2181s) -> 2.4x headroom.
  # KHONG dung 'last_run_at < now() - 90p': job 4h dang chay co last_run_at cach 4h -> bao nham.
  $claimed = @(Invoke-Psql "SELECT name FROM cron_jobs WHERE enabled AND schedule_kind IN ('cron','every') AND next_run_at IS NULL" | Where-Object { $_ })
  foreach ($k in @($state.Keys | Where-Object { $_ -like 'claim:*' })) {
    if ($claimed -notcontains $k.Substring(6)) { $state.Remove($k) }   # da chay lai -> reset dong ho
  }
  $stuck = @()
  foreach ($n in $claimed) {
    $ck = "claim:$n"
    if (-not $state[$ck]) { $state[$ck] = (Get-Date).ToString('o'); continue }
    $since = $null
    try { $since = [datetime]$state[$ck] } catch {}
    if ($since -and ((Get-Date) - $since).TotalMinutes -ge 90) { $stuck += $n }
  }

  $msgs = @()
  if ($stale) { $msgs += "khong co run thanh cong trong cua so mong doi: " + ($stale -join ', ') }
  if ($stuck) { $msgs += "KET CLAIM (next_run_at NULL >90p - se khong bao gio chay lai cho toi khi restart): " + ($stuck -join ', ') }
  if ($msgs -and $hm -ge 420 -and $hm -le 1380) {
    Send-Alert 'cron' ("GoClaw cron im lang - " + ($msgs -join ' | ') + ".")
  }

  if ($state['db-unreachable']) { $state.Remove('db-unreachable') }  # recovery - reset de lan sau alert lai
} catch {
  # Check C - DB/docker unreachable: chinh deadman cung phai len tieng.
  Send-Alert 'db-unreachable' "deadman khong query duoc goclaw-postgres-1 (docker/pg down?) - moi giam sat in-band dang mu."
}

($state | ConvertTo-Json) | Set-Content $StateFile -Encoding utf8
