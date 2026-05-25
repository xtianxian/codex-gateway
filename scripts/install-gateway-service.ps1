[CmdletBinding()]
param(
    [string]$ServiceName = "CodexGateway",
    [string]$DisplayName = "Codex Gateway",
    [switch]$Start
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Join-PathValue {
    param([string[]]$Values)

    $seen = @{}
    $parts = foreach ($value in $Values) {
        if ([string]::IsNullOrWhiteSpace($value)) {
            continue
        }

        foreach ($part in ($value -split ";")) {
            $trimmed = $part.Trim()
            if ([string]::IsNullOrWhiteSpace($trimmed)) {
                continue
            }

            $key = $trimmed.ToLowerInvariant()
            if (-not $seen.ContainsKey($key)) {
                $seen[$key] = $true
                $trimmed
            }
        }
    }

    return ($parts -join ";")
}

function Get-ServiceRecord {
    param([string]$Name)

    Get-CimInstance Win32_Service -Filter "Name='$Name'" -ErrorAction SilentlyContinue
}

function Stop-AndDeleteService {
    param([string]$Name)

    $service = Get-ServiceRecord -Name $Name
    if ($null -eq $service) {
        return
    }

    if ($service.State -ne "Stopped") {
        & sc.exe stop $Name *> $null

        $deadline = (Get-Date).AddSeconds(30)
        do {
            Start-Sleep -Seconds 1
            $service = Get-ServiceRecord -Name $Name
        } while ($null -ne $service -and $service.State -ne "Stopped" -and (Get-Date) -lt $deadline)

        if ($null -ne $service -and $service.State -ne "Stopped") {
            throw "Timed out waiting for service '$Name' to stop."
        }
    }

    & sc.exe delete $Name | Out-Null

    $deadline = (Get-Date).AddSeconds(30)
    do {
        Start-Sleep -Seconds 1
        $service = Get-ServiceRecord -Name $Name
    } while ($null -ne $service -and (Get-Date) -lt $deadline)

    if ($null -ne $service) {
        throw "Timed out waiting for service '$Name' to be deleted."
    }
}

function Move-ServiceBinary {
    param(
        [string]$Source,
        [string]$Destination
    )

    $deadline = (Get-Date).AddSeconds(30)
    while ($true) {
        try {
            if (Test-Path -LiteralPath $Destination) {
                Remove-Item -LiteralPath $Destination -Force
            }

            Move-Item -LiteralPath $Source -Destination $Destination -Force
            return
        } catch {
            if ((Get-Date) -ge $deadline) {
                throw "Failed to replace $Destination with $Source. Last error: $($_.Exception.Message)"
            }

            Start-Sleep -Seconds 1
        }
    }
}

function Get-CurrentProcessChainIds {
    $ids = New-Object 'System.Collections.Generic.HashSet[int]'
    $processId = $PID
    while ($processId -gt 0) {
        if (-not $ids.Add([int]$processId)) {
            break
        }
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
        if ($null -eq $process) {
            break
        }
        $processId = [int]$process.ParentProcessId
    }
    return $ids
}

function Add-DescendantProcessIds {
    param(
        [Parameter(Mandatory = $true)]
        [object[]]$Processes,
        [Parameter(Mandatory = $true)]
        [int]$ProcessId,
        [Parameter(Mandatory = $true)]
        [System.Collections.Generic.HashSet[int]]$TargetIds,
        [Parameter(Mandatory = $true)]
        [System.Collections.Generic.HashSet[int]]$ExcludedIds
    )

    foreach ($child in $Processes | Where-Object { [int]$_.ParentProcessId -eq $ProcessId }) {
        $childId = [int]$child.ProcessId
        if ($ExcludedIds.Contains($childId)) {
            continue
        }
        if ($TargetIds.Add($childId)) {
            Add-DescendantProcessIds -Processes $Processes -ProcessId $childId -TargetIds $TargetIds -ExcludedIds $ExcludedIds
        }
    }
}

function Stop-ExistingGatewayRuns {
    param([string]$ProjectRoot)

    $allProcesses = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    $currentChainIds = Get-CurrentProcessChainIds
    $targetIds = New-Object 'System.Collections.Generic.HashSet[int]'
    $escapedProjectRoot = [regex]::Escape($ProjectRoot)

    foreach ($process in $allProcesses) {
        $processId = [int]$process.ProcessId
        if ($currentChainIds.Contains($processId)) {
            continue
        }

        $commandLine = [string]$process.CommandLine
        if (-not $commandLine) {
            continue
        }

        $isThisGateway =
            $commandLine -match $escapedProjectRoot -and (
                $commandLine -match "start-gateway\.(ps1|vbs)" -or
                ($commandLine -match "codex-gateway(\.exe)?" -and $commandLine -match "telegram run")
            )

        if ($isThisGateway -and $targetIds.Add($processId)) {
            Add-DescendantProcessIds -Processes $allProcesses -ProcessId $processId -TargetIds $targetIds -ExcludedIds $currentChainIds
        }
    }

    foreach ($targetId in $targetIds) {
        Stop-Process -Id $targetId -Force -ErrorAction SilentlyContinue
    }
}

if ($env:CODEX_GATEWAY_TEST_SERVICE_INSTALL_LOG) {
    "install ServiceName=$ServiceName DisplayName=$DisplayName Start=$Start" |
        Out-File -FilePath $env:CODEX_GATEWAY_TEST_SERVICE_INSTALL_LOG -Encoding utf8 -Append
    return
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path (Join-Path $scriptDir "..")).Path
$source = Join-Path $scriptDir "CodexGatewayService.cs"
$serviceDir = Join-Path $repoRoot ".codex-gateway\service"
$serviceExe = Join-Path $serviceDir "CodexGatewayService.exe"
$csc = "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"

if (-not (Test-Path -LiteralPath $csc)) {
    throw "C# compiler not found at $csc."
}

New-Item -ItemType Directory -Force -Path $serviceDir | Out-Null

$isAdministrator = Test-IsAdministrator
$buildExe = Join-Path $serviceDir ("CodexGatewayService.build.{0}.exe" -f ([Guid]::NewGuid().ToString("N")))

& $csc `
    /nologo `
    /target:winexe `
    /out:$buildExe `
    /reference:System.ServiceProcess.dll `
    /reference:System.Management.dll `
    $source

if ($LASTEXITCODE -ne 0) {
    throw "Failed to compile $source."
}

if (-not $isAdministrator) {
    Remove-Item -LiteralPath $buildExe -Force -ErrorAction SilentlyContinue
    throw "Compiled staged service binary successfully. Re-run this script from an elevated PowerShell window to install the Windows service."
}

Stop-AndDeleteService -Name $ServiceName
Move-ServiceBinary -Source $buildExe -Destination $serviceExe
Stop-ExistingGatewayRuns -ProjectRoot $repoRoot

& sc.exe create $ServiceName binPath= "`"$serviceExe`"" start= auto DisplayName= $DisplayName | Out-Null
& sc.exe config $ServiceName start= delayed-auto | Out-Null
& sc.exe description $ServiceName "Codex Gateway Telegram bridge for local Codex app-server." | Out-Null
& sc.exe failure $ServiceName reset= 86400 actions= restart/15000/restart/30000/""/0 | Out-Null
& sc.exe failureflag $ServiceName 1 | Out-Null

$servicePath = Join-PathValue @(
    [Environment]::GetEnvironmentVariable("Path", "Machine"),
    [Environment]::GetEnvironmentVariable("Path", "User")
)

if ([string]::IsNullOrWhiteSpace($servicePath)) {
    throw "Unable to build service PATH from Machine and User PATH."
}

$environment = [System.Collections.Generic.List[string]]::new()
$environment.Add("Path=$servicePath")
foreach ($name in @("USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA")) {
    $value = [Environment]::GetEnvironmentVariable($name, "Process")
    if (-not [string]::IsNullOrWhiteSpace($value)) {
        $environment.Add("$name=$value")
    }
}
if (-not [string]::IsNullOrWhiteSpace($env:USERPROFILE)) {
    $environment.Add("HOME=$env:USERPROFILE")
}
$codexHome = if (-not [string]::IsNullOrWhiteSpace($env:CODEX_HOME)) {
    $env:CODEX_HOME
} elseif (-not [string]::IsNullOrWhiteSpace($env:USERPROFILE)) {
    Join-Path $env:USERPROFILE ".codex"
} else {
    $null
}
if (-not [string]::IsNullOrWhiteSpace($codexHome)) {
    $environment.Add("CODEX_HOME=$codexHome")
}

$serviceRegPath = "HKLM:\SYSTEM\CurrentControlSet\Services\$ServiceName"
New-ItemProperty `
    -Path $serviceRegPath `
    -Name "Environment" `
    -PropertyType MultiString `
    -Value $environment.ToArray() `
    -Force |
    Out-Null

if ($Start) {
    & sc.exe start $ServiceName | Out-Null
}

Get-CimInstance Win32_Service -Filter "Name='$ServiceName'" |
    Select-Object Name,DisplayName,State,StartMode,PathName

Write-Host "Service environment configured for PATH, user profile, app data, and Codex home."
