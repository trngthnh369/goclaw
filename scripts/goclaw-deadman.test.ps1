# Unit test cho khoi tracking claim cua Check B trong goclaw-deadman.ps1 (copy nguyen logic,
# inject $claimed gia). Ly do test rieng: khong the set next_run_at=NULL tren DB production
# de thu that, ma day lai dung la nhanh de sai nhat - sai thi hong trong IM LANG.
#
# Chay: pwsh -NoProfile -File scripts/goclaw-deadman.test.ps1
# Sua khoi tracking trong goclaw-deadman.ps1 thi cap nhat Invoke-ClaimTracking o day cho khop.
$fail = 0
function Check($name, $cond) {
  if ($cond) { "  PASS  $name" } else { $script:fail++; "  FAIL  $name" }
}

function Invoke-ClaimTracking($state, $claimed) {
  foreach ($k in @($state.Keys | Where-Object { $_ -like 'claim:*' })) {
    if ($claimed -notcontains $k.Substring(6)) { $state.Remove($k) }
  }
  $stuck = @()
  foreach ($n in $claimed) {
    $ck = "claim:$n"
    if (-not $state[$ck]) { $state[$ck] = (Get-Date).ToString('o'); continue }
    $since = $null
    try { $since = [datetime]$state[$ck] } catch {}
    if ($since -and ((Get-Date) - $since).TotalMinutes -ge 90) { $stuck += $n }
  }
  return , $stuck
}

"T1 - lan poll dau tien thay claim: ghi nhan, KHONG alert (co the dang chay that)"
$s = @{}
$stuck = Invoke-ClaimTracking $s @('polymarket_hourly_scan')
Check "khong alert" ($stuck.Count -eq 0)
Check "da ghi timestamp" ($null -ne $s['claim:polymarket_hourly_scan'])

"T2 - van claim sau 40p (< run dai nhat 37p + margin): van KHONG alert"
$s['claim:polymarket_hourly_scan'] = (Get-Date).AddMinutes(-40).ToString('o')
$stuck = Invoke-ClaimTracking $s @('polymarket_hourly_scan')
Check "khong alert o 40p" ($stuck.Count -eq 0)

"T3 - van claim sau 95p: ALERT"
$s['claim:polymarket_hourly_scan'] = (Get-Date).AddMinutes(-95).ToString('o')
$stuck = Invoke-ClaimTracking $s @('polymarket_hourly_scan')
Check "alert o 95p" ($stuck -contains 'polymarket_hourly_scan')

"T4 - job chay lai (bien khoi danh sach claim): reset dong ho, key bi xoa"
$stuck = Invoke-ClaimTracking $s @()
Check "khong alert sau recovery" ($stuck.Count -eq 0)
Check "key da xoa" (-not $s.ContainsKey('claim:polymarket_hourly_scan'))

"T5 - khong dung cham key throttle khac"
$s = @{ 'heartbeat' = (Get-Date).ToString('o'); 'cron' = (Get-Date).ToString('o') }
$null = Invoke-ClaimTracking $s @()
Check "giu key heartbeat" ($s.ContainsKey('heartbeat'))
Check "giu key cron" ($s.ContainsKey('cron'))

"T6 - JSON round-trip: key claim: song sot qua ConvertTo/FromJson (depth 2 du vi state PHANG)"
$tmp = Join-Path $env:TEMP "deadman-rt-test.json"
$s = @{ 'claim:daily-report' = (Get-Date).AddMinutes(-120).ToString('o'); 'cron' = 'x' }
($s | ConvertTo-Json) | Set-Content $tmp -Encoding utf8
$back = @{}
(Get-Content $tmp -Raw | ConvertFrom-Json).psobject.Properties | ForEach-Object { $back[$_.Name] = $_.Value }
Check "key claim: con nguyen" ($back['claim:daily-report'] -eq $s['claim:daily-report'])
Check "parse lai thanh datetime duoc" ($null -ne ([datetime]$back['claim:daily-report']))
$stuck = Invoke-ClaimTracking $back @('daily-report')
Check "state doc lai van tinh duoc stuck" ($stuck -contains 'daily-report')
Remove-Item $tmp -Force

if ($fail -eq 0) { "`nALL PASS" } else { "`n$fail FAILED" }
