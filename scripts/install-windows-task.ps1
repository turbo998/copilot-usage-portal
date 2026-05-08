<#
.SYNOPSIS
    Registers a Windows Scheduled Task that runs the Copilot CLI collector every 5 minutes.

.PARAMETER IngestUrl
    Full URL of the /ingest endpoint, e.g. https://func-xxx.azurewebsites.net/ingest

.PARAMETER IngestKey
    Pre-shared key issued during deploy.

.PARAMETER FunctionKey
    Function-level master key. Get with: az functionapp keys list -g rg-... -n func-...

.PARAMETER TaskName
    Scheduled Task name (default: CopilotUsageCollector)
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)] [string]$IngestUrl,
    [Parameter(Mandatory=$true)] [string]$IngestKey,
    [Parameter(Mandatory=$true)] [string]$FunctionKey,
    [string]$TaskName = 'CopilotUsageCollector',
    [string]$ClientId = 'copilot-cli',
    [int]$IntervalMinutes = 5
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$collector = Join-Path $root 'collector\collector.py'
if (-not (Test-Path $collector)) { throw "Collector script not found: $collector" }

$python = (Get-Command python).Source
$logDir = Join-Path $env:USERPROFILE '.copilot\logs'

# Make sure pip deps are installed for current user
Write-Host '==> Installing collector dependencies (requests)' -ForegroundColor Cyan
& $python -m pip install --user --quiet requests

# Compose the full ingest URL with the function-app key code
if ($IngestUrl -notmatch '\?code=') {
    $sep = if ($IngestUrl -match '\?') { '&' } else { '?' }
    $fullUrl = "$IngestUrl$sep" + "code=$FunctionKey"
} else {
    $fullUrl = $IngestUrl
}

$cmdArgs = @(
    '"' + $collector + '"',
    '--client', $ClientId,
    '--source', '"' + $logDir + '"',
    '--ingest-url', '"' + $fullUrl + '"',
    '--ingest-key', $IngestKey,
    '--once'
)

$action = New-ScheduledTaskAction -Execute $python -Argument ($cmdArgs -join ' ')
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -MultipleInstances IgnoreNew

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Host "==> Removing existing task $TaskName" -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description 'Copilot Usage Portal — local CLI log collector' `
    -RunLevel Limited | Out-Null

Write-Host ''
Write-Host "==> Task '$TaskName' registered. First run starts now, then every $IntervalMinutes minutes." -ForegroundColor Green
Write-Host '    View status: Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo'
Write-Host '    Force run:   Start-ScheduledTask -TaskName $TaskName'
