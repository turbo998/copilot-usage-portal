<#
.SYNOPSIS
    Deploys the Copilot Usage Portal to Azure.

.DESCRIPTION
    1. Creates resource group (idempotent).
    2. Generates a random INGEST_KEY (64 hex) if the parameter file still has the placeholder.
    3. Runs az deployment group create with infra/main.bicep.
    4. Publishes the Functions app (api/) using Azure Functions Core Tools.
    5. Builds the web/ project and uploads to Static Web Apps using SWA CLI.
    6. Seeds the cost_table container with infra/cost_table_seed.json.

.PARAMETER ResourceGroup
    Resource group name. Defaults to rg-copilot-usage-portal.

.PARAMETER Location
    Region for all non-SWA resources. Defaults to southeastasia.

.PARAMETER SwaLocation
    Region for Static Web App. Defaults to eastasia.

.PARAMETER TenantId
    Entra ID tenant id used by Static Web Apps Easy Auth. Required for production.

.PARAMETER SkipBuild
    Skip web/ npm build step (assumes dist/ already exists).
#>
[CmdletBinding()]
param(
    [string]$ResourceGroup = 'rg-copilot-usage-portal',
    [string]$Location      = 'southeastasia',
    [string]$SwaLocation   = 'eastasia',
    [string]$TenantId      = '',
    [switch]$SkipBuild
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Assert-LastSuccess($what) {
    if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE)" }
}

function Get-DeployerPublicIp {
    foreach ($svc in @('https://api.ipify.org', 'https://ifconfig.me/ip', 'https://icanhazip.com')) {
        try {
            $ip = (Invoke-WebRequest -Uri $svc -UseBasicParsing -TimeoutSec 5).Content.Trim()
            if ($ip -match '^\d{1,3}(\.\d{1,3}){3}$') { return $ip }
        } catch { }
    }
    throw 'Could not determine deployer public IP (tried ipify/ifconfig/icanhazip).'
}

function Add-DeployerIpToStorage {
    param([string]$Rg, [string]$Sa, [string]$Ip)
    Write-Host "    + Adding $Ip to $Sa network rules (storage default=Deny)" -ForegroundColor DarkGray
    az storage account network-rule add `
        --resource-group $Rg --account-name $Sa --ip-address $Ip --output none 2>$null
    Start-Sleep -Seconds 20  # rule propagation
}

function Remove-DeployerIpFromStorage {
    param([string]$Rg, [string]$Sa, [string]$Ip)
    if (-not $Ip) { return }
    Write-Host "    - Removing $Ip from $Sa network rules" -ForegroundColor DarkGray
    az storage account network-rule remove `
        --resource-group $Rg --account-name $Sa --ip-address $Ip --output none 2>$null
}

# 0. Ensure az is logged in
Write-Step 'Verifying az login'
$acct = az account show --output json 2>$null | ConvertFrom-Json
if (-not $acct) { throw 'Run "az login" first.' }
Write-Host "    Subscription: $($acct.name) ($($acct.id))"

# 1. Resource group
Write-Step "Creating resource group $ResourceGroup in $Location"
az group create --name $ResourceGroup --location $Location --output none

# 2. Generate INGEST_KEY if needed
$paramFile = Join-Path $root 'infra\main.parameters.json'
$params = Get-Content $paramFile -Raw | ConvertFrom-Json
if ($params.parameters.collectorIngestKey.value -like 'REPLACE_*') {
    Write-Step 'Generating new INGEST_KEY (64 hex)'
    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $key = -join ($bytes | ForEach-Object { $_.ToString('x2') })
    $params.parameters.collectorIngestKey.value = $key
    $params | ConvertTo-Json -Depth 10 | Set-Content $paramFile
    Write-Host "    Saved to $paramFile (keep this file out of git!)" -ForegroundColor Yellow
}
$ingestKey = $params.parameters.collectorIngestKey.value

# 3. Deploy infra
Write-Step 'Deploying Bicep'
$deployName = "copilot-usage-$(Get-Date -Format yyyyMMddHHmmss)"
$deployJson = az deployment group create `
    --resource-group $ResourceGroup `
    --name $deployName `
    --template-file (Join-Path $root 'infra\main.bicep') `
    --parameters (Join-Path $root 'infra\main.parameters.json') `
    --parameters location=$Location swaLocation=$SwaLocation `
    --output json
Assert-LastSuccess 'Bicep deployment'
$deployResult = $deployJson | ConvertFrom-Json

$outputs = $deployResult.properties.outputs
$funcName        = $outputs.functionAppName.value
$funcHost        = $outputs.functionAppHostName.value
$swaName         = $outputs.staticWebAppName.value
$swaHost         = $outputs.staticWebAppHostName.value
$cosmosEndpoint  = $outputs.cosmosEndpoint.value
$cosmosDb        = $outputs.cosmosDatabaseName.value
$storageName     = $outputs.storageAccountName.value

Write-Host "    Function App  : $funcName ($funcHost)"
Write-Host "    Static Web App: $swaName ($swaHost)"
Write-Host "    Cosmos DB     : $cosmosEndpoint / $cosmosDb"
Write-Host "    Storage       : $storageName"

# 4. Publish Functions
# Storage is locked down (networkAcls.defaultAction=Deny) so the local zip-upload
# fails unless we punch a temporary hole for the deployer's public IP. We always
# remove the rule afterwards (try/finally), and leave defaultAction=Deny so the
# storage stays MCAPS-friendly.
Write-Step 'Publishing Functions (func azure functionapp publish)'
$deployerIp = $null
try {
    $deployerIp = Get-DeployerPublicIp
    Write-Host "    Deployer IP: $deployerIp"
    # Ensure publicNetworkAccess is Enabled (MCAPS may have flipped it).
    az storage account update -g $ResourceGroup -n $storageName --public-network-access Enabled --output none 2>$null
    Add-DeployerIpToStorage -Rg $ResourceGroup -Sa $storageName -Ip $deployerIp
    Push-Location (Join-Path $root 'api')
    try {
        if (-not (Get-Command func -ErrorAction SilentlyContinue)) {
            throw 'Azure Functions Core Tools (func) not found. Install via: npm i -g azure-functions-core-tools@4'
        }
        func azure functionapp publish $funcName --python --build remote
        Assert-LastSuccess 'func publish'
    } finally { Pop-Location }
} finally {
    Remove-DeployerIpFromStorage -Rg $ResourceGroup -Sa $storageName -Ip $deployerIp
}

# 5. Build & deploy web
if (-not $SkipBuild) {
    Write-Step 'Building web/'
    Push-Location (Join-Path $root 'web')
    try {
        npm install --no-audit --no-fund --loglevel=error
        npm run build
    } finally { Pop-Location }
}

# Substitute tenant id in staticwebapp.config.json
if ($TenantId) {
    $cfgSrc = Join-Path $root 'web\staticwebapp.config.json'
    $cfgDst = Join-Path $root 'web\dist\staticwebapp.config.json'
    (Get-Content $cfgSrc -Raw).Replace('__TENANT_ID__', $TenantId) | Set-Content $cfgDst -Encoding UTF8
} else {
    Write-Host 'WARNING: TenantId not provided — Easy Auth will not be configured. Pass -TenantId xxx-xxx for production.' -ForegroundColor Yellow
    Copy-Item (Join-Path $root 'web\staticwebapp.config.json') (Join-Path $root 'web\dist\staticwebapp.config.json') -Force
}

Write-Step 'Uploading web/dist via SWA deployment token'
$swaToken = az staticwebapp secrets list --name $swaName --resource-group $ResourceGroup --query 'properties.apiKey' --output tsv
Assert-LastSuccess 'reading SWA deployment token'
if (-not $swaToken) { throw "Failed to read deployment token for SWA $swaName" }

if (-not (Get-Command swa -ErrorAction SilentlyContinue)) {
    Write-Host '    Installing @azure/static-web-apps-cli locally'
    npm install -g @azure/static-web-apps-cli --loglevel=error
}
swa deploy (Join-Path $root 'web\dist') --deployment-token $swaToken --env production

# 6. Seed cost table
# After VNet+PE rollout, the deployer machine can't reach Cosmos directly
# (publicNetworkAccess may be Disabled). We call the Function App's bundled
# /api/metrics/seed-cost-table endpoint instead — it runs inside the VNet and
# upserts via MI over the Cosmos Private Endpoint.
Write-Step 'Seeding cost_table via Function endpoint'
$seedUrl = "https://$funcHost/api/metrics/seed-cost-table"
$maxAttempts = 5
for ($i=1; $i -le $maxAttempts; $i++) {
    try {
        $resp = Invoke-RestMethod -Uri $seedUrl -Method Post `
            -Headers @{ 'X-Collector-Key' = $ingestKey } -TimeoutSec 60
        Write-Host "    Upserted: $($resp.upserted) row(s); failed: $($resp.failed.Count)"
        if ($resp.failed -and $resp.failed.Count -gt 0) {
            Write-Host "    Failures (first 3):" -ForegroundColor Yellow
            $resp.failed | Select-Object -First 3 | ForEach-Object { Write-Host "      $($_ | ConvertTo-Json -Compress)" }
        }
        break
    } catch {
        $code = $_.Exception.Response.StatusCode.value__
        if ($i -lt $maxAttempts -and ($code -in 0,502,503,504)) {
            Write-Host "    Attempt $i/$maxAttempts failed (HTTP $code), retrying in 10s..." -ForegroundColor Yellow
            Start-Sleep -Seconds 10
        } else {
            throw "Seed via Function endpoint failed (HTTP $code): $($_.Exception.Message)"
        }
    }
}

Write-Host ''
Write-Host '==> Deployment complete.' -ForegroundColor Green
Write-Host ''
Write-Host "Portal URL  : https://$swaHost"
Write-Host "Ingest URL  : https://$funcHost/ingest"
Write-Host "Ingest key  : $ingestKey"
Write-Host ''
Write-Host 'Next: configure collectors with these values, then visit the portal.' -ForegroundColor Cyan
