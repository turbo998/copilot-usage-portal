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

Write-Host "    Function App  : $funcName ($funcHost)"
Write-Host "    Static Web App: $swaName ($swaHost)"
Write-Host "    Cosmos DB     : $cosmosEndpoint / $cosmosDb"

# 4. Publish Functions
Write-Step 'Publishing Functions (func azure functionapp publish)'
Push-Location (Join-Path $root 'api')
try {
    if (-not (Get-Command func -ErrorAction SilentlyContinue)) {
        throw 'Azure Functions Core Tools (func) not found. Install via: npm i -g azure-functions-core-tools@4'
    }
    func azure functionapp publish $funcName --python --build remote
} finally { Pop-Location }

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
Write-Step 'Seeding cost_table container'
$pyArgs = @(
    Join-Path $root 'scripts\seed_cost_table.py'
    '--endpoint', $cosmosEndpoint
    '--database', $cosmosDb
    '--seed-file', (Join-Path $root 'infra\cost_table_seed.json')
)
python @pyArgs

Write-Host ''
Write-Host '==> Deployment complete.' -ForegroundColor Green
Write-Host ''
Write-Host "Portal URL  : https://$swaHost"
Write-Host "Ingest URL  : https://$funcHost/ingest"
Write-Host "Ingest key  : $ingestKey"
Write-Host ''
Write-Host 'Next: configure collectors with these values, then visit the portal.' -ForegroundColor Cyan
