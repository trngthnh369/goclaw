<#
.SYNOPSIS
  Apply or verify the GoClaw tier-policy.json against the live DB via HTTP API.
.DESCRIPTION
  -Apply   PUT each agent's provider/model/model_fallback to match the policy file.
  -Verify  Compare DB state with policy and report drift. No writes.
  Default (no flag) = -Verify.
.EXAMPLE
  .\apply-tier-policy.ps1 -Verify
  .\apply-tier-policy.ps1 -Apply
#>
[CmdletBinding(DefaultParameterSetName = 'Verify')]
param(
  [Parameter(ParameterSetName = 'Apply')][switch]$Apply,
  [Parameter(ParameterSetName = 'Verify')][switch]$Verify
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$PolicyFile = Join-Path $Root 'tier-policy.json'

if (-not (Test-Path $PolicyFile)) { Write-Error "Policy file not found: $PolicyFile"; exit 1 }
$policy = (Get-Content -Raw $PolicyFile | ConvertFrom-Json).agents

$baseUrl = 'http://127.0.0.1:18790'
$token = $env:GOCLAW_GATEWAY_TOKEN
if (-not $token) {
  $token = docker exec goclaw-goclaw-1 sh -c 'echo $GOCLAW_GATEWAY_TOKEN' 2>$null
  $token = $token.Trim()
}
if (-not $token) { Write-Error "GOCLAW_GATEWAY_TOKEN not set and container unreachable"; exit 1 }

function Invoke-GoClaw([string]$method, [string]$path, [string]$body) {
  if ($body) {
    $tmpName = '/tmp/goclaw-policy-body.json'
    [System.IO.File]::WriteAllText("$env:TEMP\gcbody.json", $body, [System.Text.Encoding]::UTF8)
    Get-Content "$env:TEMP\gcbody.json" | docker exec -i goclaw-goclaw-1 sh -c "cat > $tmpName" 2>$null
    $raw = docker exec goclaw-goclaw-1 sh -c "curl -s -m 30 -X $method -H ""Authorization: Bearer `$GOCLAW_GATEWAY_TOKEN"" -H 'Content-Type: application/json' -d @$tmpName $baseUrl$path" 2>$null
  } else {
    $raw = docker exec goclaw-goclaw-1 sh -c "curl -s -m 30 -X $method -H ""Authorization: Bearer `$GOCLAW_GATEWAY_TOKEN"" -H 'Content-Type: application/json' $baseUrl$path" 2>$null
  }
  return $raw | ConvertFrom-Json
}

$resp = Invoke-GoClaw 'GET' '/v1/agents'
$agents = if ($resp.agents) { $resp.agents } elseif ($resp.data) { $resp.data } else { $resp }
# An empty list means the gateway is not ready or the token is wrong; treating every
# policy entry as "not found" would otherwise end in a false "No drift".
if (-not @($agents | Where-Object { $_.agent_key }).Count) {
  Write-Error "GET /v1/agents returned no agents (gateway not ready?). Refusing to report."; exit 1
}

$policyKeys = @($policy.PSObject.Properties)
$drifts = @()
$missing = @()
$applied = @()

foreach ($prop in $policyKeys) {
  $key = $prop.Name
  $want = $prop.Value
  $agent = $agents | Where-Object { $_.agent_key -eq $key }
  if (-not $agent) { Write-Warning "MISSING $key - in policy but not in DB (create it first)"; $missing += $key; continue }

  $wantFb = @{
    enabled = $true; strategy = 'priority_order'
    max_attempts = [int]$want.max_attempts; cooldown_enabled = $true
    candidates = @($want.candidates | ForEach-Object { @{ provider = $_.provider; model = $_.model } })
  }

  $match = ($agent.provider -eq $want.provider) -and ($agent.model -eq $want.model)
  if ($match) {
    $dbFb = $agent.model_fallback
    if ($dbFb -is [string]) { $dbFb = $dbFb | ConvertFrom-Json }
    $match = ($dbFb.enabled -eq $true) -and ([int]$dbFb.max_attempts -eq [int]$want.max_attempts)
    if ($match) {
      $dbCands = @($dbFb.candidates | ForEach-Object { "$($_.provider)/$($_.model)" })
      $wantCands = @($want.candidates | ForEach-Object { "$($_.provider)/$($_.model)" })
      $match = ($dbCands -join ',') -eq ($wantCands -join ',')
    }
  }

  if (-not $match) {
    $drifts += [PSCustomObject]@{
      Agent = $key
      Group = $want.group
      Want  = "$($want.provider)/$($want.model) -> $($want.candidates | ForEach-Object { "$($_.provider)/$($_.model)" } | Join-String -Separator ' -> ')"
      Have  = "$($agent.provider)/$($agent.model)"
    }

    if ($Apply) {
      $body = @{
        provider = $want.provider
        model = $want.model
        model_fallback = $wantFb
      } | ConvertTo-Json -Depth 5 -Compress
      $escapedBody = $body -replace "'", "'\''"
      try {
        Invoke-GoClaw 'PUT' "/v1/agents/$($agent.id)" $escapedBody | Out-Null
        $applied += $key
        Write-Host "  APPLIED $key" -ForegroundColor Green
      } catch {
        Write-Warning "  FAIL $key — $($_.Exception.Message)"
      }
    }
  }
}

if ($missing.Count -gt 0) {
  Write-Host "`nMISSING in DB ($($missing.Count)): $($missing -join ', ')" -ForegroundColor Yellow
}
if ($drifts.Count -eq 0 -and $missing.Count -eq 0) {
  Write-Host "`nALL $($policyKeys.Count) agents match policy. No drift." -ForegroundColor Green
} elseif ($drifts.Count -eq 0) {
  Write-Host "`n$($policyKeys.Count - $missing.Count)/$($policyKeys.Count) present agents match policy." -ForegroundColor Yellow
} else {
  Write-Host "`nDRIFT detected ($($drifts.Count) agent(s)):" -ForegroundColor Yellow
  $drifts | Format-Table -AutoSize | Out-String | Write-Host
  if (-not $Apply) {
    Write-Host "Run with -Apply to fix." -ForegroundColor Cyan
  } else {
    Write-Host "Applied: $($applied.Count)/$($drifts.Count)" -ForegroundColor Green
  }
}
