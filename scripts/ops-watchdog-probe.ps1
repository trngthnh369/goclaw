# ops-watchdog-probe.ps1 — Tier 1 health probe for GoClaw stack (every 15 min via Windows Task Scheduler).
#
# Checks (deterministic, no LLM):
#   1. Docker engine reachable
#   2. GoClaw container healthy (GET /health)
#   3. PostgreSQL container healthy (pg_isready via docker exec)
#   4. CLIProxy/Antigravity sidecar responding (1-token completion)
#
# State-change alerts to Discord #ops-alerts. Anti-spam: only on transition + 6h re-alert.
# Tier 2 (LLM diagnosis via ops-watchdog agent) fires best-effort when GoClaw is up but a
# subsystem is degraded.
#
# Setup:
#   1. Create cliproxy/probe.env:  DISCORD_WEBHOOK=https://discord.com/api/webhooks/...
#   2. Register task:
#        $ps = (Get-Command pwsh).Source
#        $arg = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File D:\Projects\personal\goclaw\scripts\ops-watchdog-probe.ps1"
#        schtasks /create /tn "GoClaw-OpsWatchdog" /tr "`"$ps`" $arg" /sc minute /mo 15 /ru SYSTEM /rl HIGHEST /f

$ErrorActionPreference = 'Continue'
$PSNativeCommandUseErrorActionPreference = $false

$ProjectDir = Split-Path $PSScriptRoot -Parent
$Docker     = 'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
if (-not (Test-Path $Docker)) { $Docker = 'docker' }

$EnvFile    = Join-Path $ProjectDir 'cliproxy\probe.env'
$ConfigYaml = Join-Path $ProjectDir 'cliproxy\config.yaml'
$StateFile  = Join-Path $ProjectDir 'scripts\_watchdog.state.json'
$LogFile    = Join-Path $ProjectDir 'scripts\_watchdog.log'
$ProbeModel = 'ag-flash-lite'
$ReAlertSec = 6 * 3600
$GoclawContainer = 'goclaw-goclaw-1'
$PgContainer     = 'goclaw-postgres-1'
$GoclawPort      = 18790

function Write-Log([string]$msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    try {
        Add-Content -LiteralPath $LogFile -Value $line -ErrorAction SilentlyContinue
        if ((Test-Path $LogFile) -and (Get-Item $LogFile).Length -gt 1MB) {
            $tail = Get-Content -LiteralPath $LogFile -Tail 500
            Set-Content -LiteralPath $LogFile -Value $tail
        }
    } catch {}
}

# --- secrets ---
$webhook = $null
if (Test-Path $EnvFile) {
    $wm = Select-String -LiteralPath $EnvFile -Pattern '^\s*DISCORD_WEBHOOK\s*=\s*(\S+)' | Select-Object -First 1
    if ($wm) { $webhook = $wm.Matches[0].Groups[1].Value }
}

$proxyKey = $null
if (Test-Path $ConfigYaml) {
    $m = Select-String -LiteralPath $ConfigYaml -Pattern '^\s*-\s*"([0-9a-f]{64})"' | Select-Object -First 1
    if ($m) { $proxyKey = $m.Matches[0].Groups[1].Value }
}

# --- gateway token (for Tier 2 LLM trigger) ---
$gwToken = $null
$dotenv = Join-Path $ProjectDir '.env'
if (Test-Path $dotenv) {
    $tm = Select-String -LiteralPath $dotenv -Pattern '^\s*GOCLAW_GATEWAY_TOKEN\s*=\s*(\S+)' | Select-Object -First 1
    if ($tm) { $gwToken = $tm.Matches[0].Groups[1].Value }
}

# ============================================================
# CHECK 1: Docker engine
# ============================================================
function Test-DockerEngine {
    & $Docker ps --format '{{.Names}}' *>$null 2>&1
    if ($LASTEXITCODE -ne 0) {
        Start-Sleep -Seconds 15
        & $Docker ps --format '{{.Names}}' *>$null 2>&1
        return ($LASTEXITCODE -eq 0)
    }
    return $true
}

# ============================================================
# CHECK 2: GoClaw /health
# ============================================================
function Test-GoclawHealth {
    try {
        $r = Invoke-RestMethod -Uri "http://localhost:$GoclawPort/health" -TimeoutSec 10 -ErrorAction Stop
        return ($r.status -eq 'ok')
    } catch {
        return $false
    }
}

# ============================================================
# CHECK 3: PostgreSQL
# ============================================================
function Test-PostgresHealth {
    & $Docker exec $PgContainer pg_isready -U goclaw -d goclaw *>$null 2>&1
    return ($LASTEXITCODE -eq 0)
}

# ============================================================
# CHECK 4: Antigravity sidecar (1-token probe)
# ============================================================
function Test-AntigravityHealth {
    if (-not $proxyKey) { return 'SKIP' }
    $inner = @'
BODY=$(curl -s -m 60 -o /tmp/probe_body -w "%{http_code}" -X POST \
  -H "Authorization: Bearer $PK" -H "Content-Type: application/json" \
  -d "{\"model\":\"__MODEL__\",\"max_tokens\":1,\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}]}" \
  http://cliproxy:8317/v1/chat/completions); echo "HTTP=$BODY"; echo "---BODY---"; cat /tmp/probe_body 2>/dev/null
'@ -replace '__MODEL__', $ProbeModel

    $out = ''
    try {
        $out = & $Docker exec -T -e "PK=$proxyKey" $GoclawContainer sh -c $inner 2>&1 | Out-String
    } catch {
        return 'ERROR'
    }

    $http = ''
    if ($out -match 'HTTP=(\d{3})') { $http = $Matches[1] }
    if ($http -eq '' -or $http -eq '000') { return 'UNREACHABLE' }
    if ($http -eq '401' -or $http -eq '403') { return 'AUTH' }
    if ($http -eq '429') { return 'QUOTA' }
    if ($http -match '^2' -and $out -match '"choices"') { return 'OK' }
    return "HTTP-$http"
}

# ============================================================
# MAIN: run all checks, derive composite state
# ============================================================
Write-Log "=== probe start ==="

$checks = [ordered]@{}
$state = 'OK'
$details = @()

# 1) Docker
if (-not (Test-DockerEngine)) {
    $state = 'DOCKER-DOWN'
    $details += 'Docker engine unreachable'
    $checks['docker'] = 'DOWN'
    Write-Log "state=$state details=$($details -join '; ')"
    # can't check anything else
} else {
    $checks['docker'] = 'OK'

    # 2) GoClaw
    if (-not (Test-GoclawHealth)) {
        $checks['goclaw'] = 'DOWN'
        $state = 'GOCLAW-DOWN'
        $details += "GoClaw /health unreachable (port $GoclawPort)"
    } else {
        $checks['goclaw'] = 'OK'
    }

    # 3) PostgreSQL
    if (-not (Test-PostgresHealth)) {
        $checks['postgres'] = 'DOWN'
        if ($state -eq 'OK') { $state = 'POSTGRES-DOWN' }
        $details += 'PostgreSQL pg_isready failed'
    } else {
        $checks['postgres'] = 'OK'
    }

    # 4) Antigravity
    $agResult = Test-AntigravityHealth
    $checks['antigravity'] = $agResult
    if ($agResult -eq 'SKIP') {
        # no proxy key, skip
    } elseif ($agResult -ne 'OK') {
        if ($state -eq 'OK') { $state = "ANTIGRAVITY-$agResult" }
        $details += "Antigravity sidecar: $agResult"
    }
}

$detailStr = if ($details.Count -gt 0) { $details -join '; ' } else { 'all checks passed' }
Write-Log "state=$state checks=$($checks | ConvertTo-Json -Compress) details=$detailStr"

# ============================================================
# STATE FILE + ANTI-SPAM
# ============================================================
$now = [int][double]::Parse((Get-Date -UFormat %s))
$prev = $null
if (Test-Path $StateFile) {
    try { $prev = Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json } catch {}
}
$lastDelivered   = if ($prev) { $prev.last_delivered_state } else { 'OK' }
$lastDeliveredAt = if ($prev) { [int]$prev.last_delivered_at } else { 0 }
$consecutiveFail = if ($prev -and $prev.consecutive_fail) { [int]$prev.consecutive_fail } else { 0 }

if ($state -ne 'OK') { $consecutiveFail++ } else { $consecutiveFail = 0 }

function Save-State($observed, $delivered, $deliveredAt, $consec) {
    $obj = [ordered]@{
        observed_state       = $observed
        observed_at          = $now
        last_delivered_state = $delivered
        last_delivered_at    = $deliveredAt
        consecutive_fail     = $consec
        checks               = $checks
    }
    $tmp = "$StateFile.tmp"
    $obj | ConvertTo-Json | Set-Content -LiteralPath $tmp
    Move-Item -LiteralPath $tmp -Destination $StateFile -Force
}

# Only alert after 2 consecutive failures (avoid transient restart noise)
$needAlert = $false
if ($state -ne $lastDelivered -and ($state -eq 'OK' -or $consecutiveFail -ge 2)) {
    $needAlert = $true
} elseif ($state -ne 'OK' -and $consecutiveFail -ge 2 -and ($now - $lastDeliveredAt) -ge $ReAlertSec) {
    $needAlert = $true
}

if (-not $needAlert) {
    Save-State $state $lastDelivered $lastDeliveredAt $consecutiveFail
    Write-Log "=== probe done (no alert needed) ==="
    exit 0
}

# ============================================================
# DISCORD ALERT
# ============================================================
$emoji = switch -Wildcard ($state) {
    'OK'               { '✅' }
    'DOCKER-DOWN'      { '🛑' }
    'GOCLAW-DOWN'      { '🛑' }
    'POSTGRES-DOWN'    { '🛑' }
    'ANTIGRAVITY-AUTH' { '🔑' }
    'ANTIGRAVITY-QUOTA'{ '⏳' }
    'ANTIGRAVITY-*'    { '⚠️' }
    default            { '❓' }
}

$guidance = switch -Wildcard ($state) {
    'OK'                { 'All services recovered.' }
    'DOCKER-DOWN'       { 'Docker engine/Desktop unreachable. Check WSL distro or restart Docker Desktop.' }
    'GOCLAW-DOWN'       { "GoClaw /health unreachable on port $GoclawPort. Check: docker ps, docker logs $GoclawContainer" }
    'POSTGRES-DOWN'     { "PostgreSQL pg_isready failed. Check: docker logs $PgContainer" }
    'ANTIGRAVITY-AUTH'  { 'CLIProxyAPI OAuth expired. Re-login: docker compose ... run --rm -p 51121:51121 cliproxy /CLIProxyAPI/CLIProxyAPI -antigravity-login -no-browser' }
    'ANTIGRAVITY-QUOTA' { 'Antigravity Ultra quota exhausted. Agents will hard-error until reset.' }
    'ANTIGRAVITY-UNREACHABLE' { 'CLIProxy container unreachable from GoClaw. Check: docker compose ... ps cliproxy' }
    default             { "Unclassified state: $state. Check _watchdog.log for details." }
}

$content = "$emoji **GoClaw watchdog: $state**`n$guidance"

$delivered = $false
if ($webhook) {
    try {
        $payload = @{ content = $content } | ConvertTo-Json -Depth 3
        Invoke-RestMethod -Uri $webhook -Method Post -ContentType 'application/json; charset=utf-8' -Body ([System.Text.Encoding]::UTF8.GetBytes($payload)) -TimeoutSec 20 | Out-Null
        $delivered = $true
    } catch {
        Write-Log "Discord POST failed: $($_.Exception.Message)"
    }
} else {
    Write-Log 'No DISCORD_WEBHOOK in probe.env — alert not delivered (log only)'
}

if ($delivered) {
    Save-State $state $state $now $consecutiveFail
    Write-Log "alert delivered: $state"
} else {
    Save-State $state $lastDelivered $lastDeliveredAt $consecutiveFail
    Write-Log "alert PENDING (delivery failed or no webhook): $state"
}

# ============================================================
# TIER 2: LLM diagnosis (best-effort, only when GoClaw is up)
# ============================================================
if ($state -ne 'OK' -and $state -ne 'DOCKER-DOWN' -and $state -ne 'GOCLAW-DOWN' -and $gwToken -and $checks['goclaw'] -eq 'OK') {
    Write-Log "Tier 2: triggering ops-watchdog agent for diagnosis..."
    $diagPrompt = "AUTOMATED ALERT: GoClaw watchdog detected state=$state. Details: $detailStr. Check values: $($checks | ConvertTo-Json -Compress). Please analyze recent logs, identify the root cause, and suggest a fix. Be concise."
    $diagBody = @{
        model    = 'ag-pro'
        agent    = 'ops-watchdog'
        messages = @(@{ role = 'user'; content = $diagPrompt })
        max_tokens = 1000
    } | ConvertTo-Json -Depth 4

    try {
        $headers = @{ 'Authorization' = "Bearer $gwToken"; 'Content-Type' = 'application/json' }
        $diagResp = Invoke-RestMethod -Uri "http://localhost:$GoclawPort/v1/chat/completions" `
            -Method Post -Headers $headers `
            -Body ([System.Text.Encoding]::UTF8.GetBytes($diagBody)) `
            -TimeoutSec 120 -ErrorAction Stop
        $diagText = $diagResp.choices[0].message.content
        Write-Log "Tier 2 diagnosis: $($diagText.Substring(0, [Math]::Min(500, $diagText.Length)))"

        # Post diagnosis to Discord (if webhook available and diagnosis succeeded)
        if ($webhook -and $diagText) {
            $diagContent = "🔍 **ops-watchdog diagnosis ($state):**`n$($diagText.Substring(0, [Math]::Min(1800, $diagText.Length)))"
            $diagPayload = @{ content = $diagContent } | ConvertTo-Json -Depth 3
            Invoke-RestMethod -Uri $webhook -Method Post -ContentType 'application/json; charset=utf-8' -Body ([System.Text.Encoding]::UTF8.GetBytes($diagPayload)) -TimeoutSec 20 | Out-Null
            Write-Log "Tier 2 diagnosis posted to Discord"
        }
    } catch {
        Write-Log "Tier 2 FAILED (best-effort): $($_.Exception.Message)"
    }
}

Write-Log "=== probe done ==="
