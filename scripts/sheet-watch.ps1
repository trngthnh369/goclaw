# sheet-watch.ps1 - Phat hien Google Sheet vận hành NGUNG CAP NHAT (drift) truoc khi qua muon.
#
# Pain point that: row-count/pipeline drift tung bi phat hien tre 20+ ngay. Script nay chay host-side
# bang service account co san (GOOGLE_APPLICATION_CREDENTIALS) - KHONG can dua credential vao container,
# KHONG ton token LLM, deterministic.
#
# Dang ky:
#   $ps = (Get-Command pwsh).Source
#   schtasks /create /tn "GoClaw-Sheet-Watch" /tr "`"$ps`" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File D:\Projects\personal\goclaw\scripts\sheet-watch.ps1" /sc hourly /mo 6 /f
#
# Lan chay dau: tu sinh watchlist = cac sheet dang hoat dong (modified <= SeedActiveDays), ghi ra
# cliproxy/sheet-watch-config.json de user chinh tay. KHONG alert o lan dau.
#
# Chong false-positive (bai hoc plan-review agy-r2-f03): nguong mac dinh 4 ngay de phu cuoi tuan +
# 1 ngay le; sheet ngoai watchlist khong bao gio alert; moi sheet chi alert 1 lan / 24h.

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $Root
$EnvFile = Join-Path $RepoRoot 'cliproxy\probe.env'
$ConfigFile = Join-Path $RepoRoot 'cliproxy\sheet-watch-config.json'
$StateFile = Join-Path $RepoRoot 'cliproxy\sheet-watch-state.json'
$LogFile = Join-Path $RepoRoot 'cliproxy\_sheet-watch.log'
$SeedActiveDays = 2       # sheet moi hon nguong nay -> coi la "dang chay" -> vao watchlist khi seed
$DefaultMaxStaleDays = 4  # phu cuoi tuan (2) + 1 ngay le + 1 bien
$ReAlertHours = 24

function Write-Log([string]$m) {
  Add-Content -LiteralPath $LogFile -Value ("[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m)
}

if (-not (Test-Path $EnvFile)) { exit 0 }
$webhook = $null
foreach ($line in Get-Content $EnvFile) {
  if ($line -match '^\s*DISCORD_WEBHOOK\s*=\s*(.+)$') { $webhook = $Matches[1].Trim() }
}
if (-not $webhook) { exit 0 }

function Send-Discord([string]$msg) {
  try {
    Invoke-RestMethod -Uri $script:webhook -Method Post -ContentType 'application/json' `
      -Body (@{content = $msg} | ConvertTo-Json) | Out-Null
    return $true
  } catch { Write-Log "discord post failed: $($_.Exception.Message)"; return $false }
}

# --- Lay danh sach spreadsheet qua SA ---
$params = '{"pageSize":100,"q":"mimeType=''application/vnd.google-apps.spreadsheet'' and trashed=false","fields":"files(id,name,modifiedTime)","orderBy":"modifiedTime desc"}'
try {
  $raw = & gws drive files list --params $params --page-all 2>&1 | Out-String
  $files = (($raw -replace '^Using keyring.*?\r?\n','') | ConvertFrom-Json).files
} catch {
  Send-Discord "[SHEET-WATCH] Khong query duoc Google Drive (SA loi hoac het quyen) - KHONG PHAI 'moi thu on'. Kiem tra GOOGLE_APPLICATION_CREDENTIALS." | Out-Null
  Write-Log "drive query failed: $($_.Exception.Message)"
  exit 1
}
if (-not $files) { Write-Log "drive tra ve 0 file - bat thuong, khong seed"; exit 1 }
$files = @($files)
$now = Get-Date

# --- Config: seed lan dau ---
if (-not (Test-Path $ConfigFile)) {
  $seed = @($files | Where-Object { ($now - [datetime]$_.modifiedTime).TotalDays -le $SeedActiveDays } |
    ForEach-Object { [pscustomobject]@{ id = $_.id; name = $_.name; maxStaleDays = $DefaultMaxStaleDays } })
  ([pscustomobject]@{
    _comment = "maxStaleDays: so ngay khong cap nhat thi bao dong. Xoa dong khoi 'watch' de ngung theo doi sheet do."
    defaultMaxStaleDays = $DefaultMaxStaleDays
    watch = $seed
  } | ConvertTo-Json -Depth 5) | Set-Content $ConfigFile -Encoding utf8
  Write-Log "seed config voi $($seed.Count) sheet dang hoat dong (khong alert lan dau)"
  exit 0
}

$cfg = Get-Content $ConfigFile -Raw | ConvertFrom-Json
$watch = @($cfg.watch)
if ($watch.Count -eq 0) { Write-Log "watchlist rong - bo qua"; exit 0 }

$state = @{}
if (Test-Path $StateFile) {
  try { (Get-Content $StateFile -Raw | ConvertFrom-Json).psobject.Properties | ForEach-Object { $state[$_.Name] = $_.Value } } catch {}
}

$byId = @{}
foreach ($f in $files) { $byId[$f.id] = $f }

$stale = @()
$missing = @()
foreach ($w in $watch) {
  # LUU Y: dung $null -ne ... chu KHONG dung if ($w.maxStaleDays) - PowerShell coi 0 la falsy,
  # nen nguong 0 (bao ngay khi sheet khong doi trong ngay) se bi bo qua ve mac dinh.
  $limit = if ($null -ne $w.maxStaleDays) { [double]$w.maxStaleDays }
           elseif ($null -ne $cfg.defaultMaxStaleDays) { [double]$cfg.defaultMaxStaleDays }
           else { [double]$DefaultMaxStaleDays }
  $f = $byId[$w.id]
  if (-not $f) { $missing += $w.name; continue }   # bi xoa / mat quyen SA = pipeline gay
  $days = ($now - [datetime]$f.modifiedTime).TotalDays
  if ($days -gt $limit) { $stale += [pscustomobject]@{ name = $w.name; days = [int]$days; limit = $limit } }
}

# --- Alert, moi sheet toi da 1 lan / ReAlertHours ---
$lines = @()
foreach ($s in ($stale | Sort-Object -Property days -Descending)) {
  $key = "stale:$($s.name)"
  $last = $null
  if ($state[$key]) { try { $last = [datetime]$state[$key] } catch {} }
  if ($last -and ($now - $last).TotalHours -lt $ReAlertHours) { continue }
  $lines += "- $($s.name): $($s.days) ngay khong cap nhat (nguong $($s.limit)d)"
  $state[$key] = $now.ToString('o')
}
foreach ($m in $missing) {
  $key = "missing:$m"
  $last = $null
  if ($state[$key]) { try { $last = [datetime]$state[$key] } catch {} }
  if ($last -and ($now - $last).TotalHours -lt $ReAlertHours) { continue }
  $lines += "- ${m}: KHONG CON THAY (bi xoa hoac SA mat quyen)"
  $state[$key] = $now.ToString('o')
}

if ($lines.Count -gt 0) {
  $body = "[SHEET-WATCH] $($lines.Count) sheet van hanh co van de:`n" + (($lines | Select-Object -First 10) -join "`n")
  if ($lines.Count -gt 10) { $body += "`n... va $($lines.Count - 10) sheet khac" }
  if (-not (Send-Discord $body)) {
    # Gui that bai -> KHONG ghi state moi xuong dia, de lan chay sau bao lai (khong nuot canh bao).
    Write-Log "alert FAILED, khong luu state de retry lan sau"
    exit 1
  }
  Write-Log "alerted $($lines.Count) sheet (stale=$($stale.Count), missing=$($missing.Count))"
} else {
  Write-Log "ok - $($watch.Count) sheet trong watchlist deu tuoi"
}

($state | ConvertTo-Json) | Set-Content $StateFile -Encoding utf8
