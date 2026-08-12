# goclaw-deadman.ps1 - Dead-man's-switch cho heartbeat + daily-ops-digest (plan PA P0.2, agy-r2-f03)
# Companion cua cliproxy/probe-antigravity.ps1 (KHONG sua probe - script rieng, dung chung probe.env).
# ASCII-only: chay duoi pwsh/powershell scheduled task - khong emoji/unicode trong string.
#
# Dang ky (KHONG dung schtasks - xem canh bao Priority ben duoi):
#   $vbs = 'D:\Projects\personal\goclaw\scripts\run-hidden.vbs'
#   $act = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\wscript.exe" `
#     -Argument "//nologo `"$vbs`" pwsh -NoProfile -NonInteractive -ExecutionPolicy Bypass -File D:\Projects\personal\goclaw\scripts\goclaw-deadman.ps1"
#   $trg = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 30)
#   $set = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
#   $set.Priority = 5    # BAT BUOC - xem duoi
#   $set.DisallowStartIfOnBatteries = $false; $set.StopIfGoingOnBatteries = $false   # laptop rut sac = ngung giam sat
#   Register-ScheduledTask -TaskName 'GoClaw-Deadman' -Action $act -Trigger $trg -Settings $set -Force
#
# ⚠ Priority = 5, KHONG de mac dinh: schtasks va Register-ScheduledTask deu mac dinh Priority=7,
# Windows map sang BELOW_NORMAL + background I/O va bop chet docker CLI. Do 2026-08-12 cung mot
# phut: duoi scheduler `docker version` (lenh thuan client, khong cham daemon) TIMEOUT >30s va
# `docker ps` mat 27s, trong khi shell interactive chay ca hai duoi 1s; doi moi Priority=5 thi
# cung probe do xong trong 8s. Day la ly do ops-watchdog khong ghi noi state file suot 3 tuan.
# run-hidden.vbs de khong bung console moi 30 phut (S4U can quyen admin nen khong dung duoc).
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
    # -TimeoutSec bat buoc: treo o day thi state khong duoc ghi va task chay den khi bi
    # ExecutionTimeLimit giet - fail-silent y het truong hop docker CLI ket.
    Invoke-RestMethod -Uri $script:webhook -Method Post -ContentType 'application/json' -TimeoutSec 20 `
      -Body (@{content = "[DEADMAN] $msg"} | ConvertTo-Json) | Out-Null
    $state[$key] = $now.ToString('o')
  } catch {}
}

# docker CLI CO THE KET VINH VIEN, khong phai gia thuyet: 2026-08-12 quan sat 3 tien trinh
# `docker exec goclaw-postgres-1` treo 124-220s (toi khi kill) TRONG KHI `docker ps` va mot
# `docker exec` goi tay van tra loi trong 1s. Khong co timeout thi deadman treo den luc bi
# ExecutionTimeLimit giet -> khong ghi state, khong alert: dung kieu fail-silent no sinh ra de chong.
# Timeout -> throw -> catch ngoai -> alert 'db-unreachable'. stdin lay tu file rong de docker
# thay EOF ngay (chay duoi console an, stdin handle co the khong hop le).
$PsqlTimeoutSec = 45
$NullIn = Join-Path $env:TEMP 'goclaw-deadman-stdin.null'

function Invoke-Psql([string]$sql) {
  if (-not (Test-Path $script:NullIn)) { New-Item -ItemType File -Path $script:NullIn -Force | Out-Null }
  $o = [System.IO.Path]::GetTempFileName()
  $e = [System.IO.Path]::GetTempFileName()
  try {
    $p = Start-Process -FilePath 'docker' -PassThru -NoNewWindow `
      -ArgumentList ('exec goclaw-postgres-1 psql -U goclaw -d goclaw -Atc "' + $sql + '"') `
      -RedirectStandardInput $script:NullIn -RedirectStandardOutput $o -RedirectStandardError $e
    if (-not $p.WaitForExit($script:PsqlTimeoutSec * 1000)) {
      try { $p.Kill($true) } catch {}
      throw "psql timeout $($script:PsqlTimeoutSec)s"
    }
    if ($p.ExitCode -ne 0) { throw "psql exit $($p.ExitCode)" }
    return @(Get-Content $o | Where-Object { $_ -ne '' })
  } finally {
    Remove-Item $o, $e -Force -ErrorAction SilentlyContinue
  }
}

$ict = [System.TimeZoneInfo]::ConvertTimeBySystemTimeZoneId((Get-Date).ToUniversalTime(), 'SE Asia Standard Time')
$hm = $ict.Hour * 60 + $ict.Minute

try {
  # MOT lan docker exec cho ca 3 check, khong phai ba: moi lan goi la mot tien trinh docker CLI
  # rieng va CLI nay co the ket (xem chu thich Invoke-Psql) -> ba lan goi = nhan ba be mat rui ro.
  # Query luon chay day du; viec gate theo gio nam o cho ALERT, khong o cho query.
  $rows = Invoke-Psql ("SELECT 'hb|' || a.agent_key || ':' || COALESCE(h.last_status,'never') FROM agent_heartbeats h JOIN agents a ON a.id = h.agent_id WHERE h.enabled AND (now() > h.next_run_at + (h.interval_sec * interval '1 second') OR h.last_status = 'error')" +
    " UNION ALL SELECT 'stale|' || j.name || ' (' || round(extract(epoch FROM now() - s.last_ok) / 3600) || 'h khong success)' FROM cron_jobs j JOIN LATERAL (SELECT max(ran_at) AS last_ok FROM cron_run_logs l WHERE l.job_id = j.id AND l.error IS NULL) s ON true WHERE j.enabled AND j.schedule_kind IN ('cron','every') AND j.next_run_at IS NOT NULL AND j.last_run_at IS NOT NULL AND j.next_run_at > j.last_run_at AND s.last_ok IS NOT NULL AND now() - s.last_ok > (j.next_run_at - j.last_run_at) + interval '90 minutes'" +
    " UNION ALL SELECT 'claim|' || name FROM cron_jobs WHERE enabled AND schedule_kind IN ('cron','every') AND next_run_at IS NULL")

  $hb = @($rows | Where-Object { $_ -like 'hb|*' }    | ForEach-Object { $_.Substring(3) })
  $stale = @($rows | Where-Object { $_ -like 'stale|*' } | ForEach-Object { $_.Substring(6) })
  $claimed = @($rows | Where-Object { $_ -like 'claim|*' } | ForEach-Object { $_.Substring(6) })

  # Check A - heartbeat overdue/error. CHI alert trong 09:00-23:00 ICT. 0 heartbeat enabled -> rong.
  if ($hb -and $hm -ge 540 -and $hm -le 1380) {
    Send-Alert 'heartbeat' "GoClaw heartbeat overdue/error: $($hb -join ', ') - ticker chet, agent stuck, hoac provider loi (stack fail-silent, khong tu bao)."
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
  # Tracking claim chay 24/24 (khong gate theo gio) - grace 90p = 3 chu ky poll, trong khi run
  # dai nhat tung do la 37p (max duration_ms 2219397, polymarket p95 2181s) -> 2.4x headroom.
  # KHONG dung 'last_run_at < now() - 90p': job 4h dang chay co last_run_at cach 4h -> bao nham.
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
