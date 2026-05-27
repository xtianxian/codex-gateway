[CmdletBinding()]
param(
    [string]$ServiceName = "CodexGateway"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-ServiceRecord {
    param([string]$Name)

    Get-Service -Name $Name -ErrorAction SilentlyContinue
}

if ($env:CODEX_GATEWAY_TEST_SERVICE_UNINSTALL_LOG) {
    "uninstall ServiceName=$ServiceName" |
        Out-File -FilePath $env:CODEX_GATEWAY_TEST_SERVICE_UNINSTALL_LOG -Encoding utf8 -Append
    return
}

$service = Get-ServiceRecord -Name $ServiceName
if ($null -eq $service) {
    Write-Host "No Windows Service found: $ServiceName"
    exit 0
}

if ($service.Status -ne "Stopped") {
    & sc.exe stop $ServiceName *> $null

    $deadline = (Get-Date).AddSeconds(30)
    do {
        Start-Sleep -Seconds 1
        $service = Get-ServiceRecord -Name $ServiceName
    } while ($null -ne $service -and $service.Status -ne "Stopped" -and (Get-Date) -lt $deadline)

    if ($null -ne $service -and $service.Status -ne "Stopped") {
        throw "Timed out waiting for service '$ServiceName' to stop."
    }
}

& sc.exe delete $ServiceName | Out-Null

$deadline = (Get-Date).AddSeconds(30)
do {
    Start-Sleep -Seconds 1
    $service = Get-ServiceRecord -Name $ServiceName
} while ($null -ne $service -and (Get-Date) -lt $deadline)

if ($null -ne $service) {
    throw "Timed out waiting for service '$ServiceName' to be deleted."
}

Write-Host "Removed Windows Service: $ServiceName"
