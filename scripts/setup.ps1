[CmdletBinding()]
param(
    [switch]$InstallUv,
    [switch]$RemoveStartup,
    [switch]$SkipTelegramSetup,
    [switch]$SkipStartup
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$InstallCommandText = 'powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"'
$ServiceName = "CodexGateway"
$ServiceDisplayName = "Codex Gateway"
$InstallServiceScript = Join-Path $PSScriptRoot "install-gateway-service.ps1"
$UninstallServiceScript = Join-Path $PSScriptRoot "uninstall-gateway-service.ps1"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$LogDirectory = Join-Path $ProjectRoot ".codex-gateway\logs\service"
$LocalAppDataRoot = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $env:USERPROFILE "AppData\Local" }
$PytestTempRoot = Join-Path $LocalAppDataRoot "codex-gateway\pytest-temp"

function Get-UvCommand {
    Get-Command uv -ErrorAction SilentlyContinue
}

function Add-UvInstallPaths {
    $paths = @()

    if ($env:USERPROFILE) {
        $paths += (Join-Path $env:USERPROFILE ".local\bin")
        $paths += (Join-Path $env:USERPROFILE ".cargo\bin")
    }

    foreach ($path in $paths) {
        if ((Test-Path $path) -and (($env:Path -split ";") -notcontains $path)) {
            $env:Path = "$path;$env:Path"
        }
    }
}

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Test-StartupServiceExists {
    if ($null -ne $env:CODEX_GATEWAY_TEST_SERVICE_EXISTS) {
        $normalized = $env:CODEX_GATEWAY_TEST_SERVICE_EXISTS.Trim().ToLowerInvariant()
        return $normalized -eq "1" -or $normalized -eq "true" -or $normalized -eq "yes"
    }

    return $null -ne (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue)
}

function Assert-ServiceElevation {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Action
    )

    if ($env:CODEX_GATEWAY_TEST_SERVICE_INSTALL_LOG -or $env:CODEX_GATEWAY_TEST_SERVICE_UNINSTALL_LOG) {
        return
    }

    if (-not (Test-IsAdministrator)) {
        throw "$Action the Windows Service requires an elevated PowerShell. Re-run this command from an elevated PowerShell, or use -SkipStartup for non-admin setup."
    }
}

function Invoke-Uv {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    & uv @Arguments
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

function Invoke-ProjectTests {
    New-Item -ItemType Directory -Force -Path $PytestTempRoot | Out-Null

    $previousTempRoot = $env:PYTEST_DEBUG_TEMPROOT
    $previousPytestAddopts = $env:PYTEST_ADDOPTS
    try {
        $env:PYTEST_DEBUG_TEMPROOT = $PytestTempRoot
        if ([string]::IsNullOrWhiteSpace($previousPytestAddopts)) {
            $env:PYTEST_ADDOPTS = "-p no:cacheprovider"
        }
        else {
            $env:PYTEST_ADDOPTS = "$previousPytestAddopts -p no:cacheprovider"
        }
        Invoke-Uv -Arguments @("run", "pytest")
    }
    finally {
        if ($null -eq $previousTempRoot) {
            Remove-Item Env:\PYTEST_DEBUG_TEMPROOT -ErrorAction SilentlyContinue
        }
        else {
            $env:PYTEST_DEBUG_TEMPROOT = $previousTempRoot
        }

        if ($null -eq $previousPytestAddopts) {
            Remove-Item Env:\PYTEST_ADDOPTS -ErrorAction SilentlyContinue
        }
        else {
            $env:PYTEST_ADDOPTS = $previousPytestAddopts
        }
    }
}

function Read-YesNo {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Prompt,
        [Parameter(Mandatory = $true)]
        [bool]$Default
    )

    $suffix = if ($Default) { "[Y/n]" } else { "[y/N]" }
    while ($true) {
        Write-Host -NoNewline "$Prompt $suffix "
        $answer = [Console]::In.ReadLine()
        if ($null -eq $answer) {
            return $Default
        }

        $normalized = $answer.Trim().ToLowerInvariant()
        if ($normalized -eq "") {
            return $Default
        }
        if ($normalized -eq "y" -or $normalized -eq "yes") {
            return $true
        }
        if ($normalized -eq "n" -or $normalized -eq "no") {
            return $false
        }

        Write-Host "Please answer y or n."
    }
}

function Install-StartupService {
    param([bool]$Existing)

    Assert-ServiceElevation -Action "Installing and starting"
    & $InstallServiceScript -ServiceName $ServiceName -DisplayName $ServiceDisplayName -Start
    if ($Existing) {
        Write-Host "Windows Service updated and restarted: $ServiceName"
    }
    else {
        Write-Host "Windows Service installed and started: $ServiceName"
    }
    Write-Host "Logs: $LogDirectory"
}

function Remove-StartupService {
    Assert-ServiceElevation -Action "Removing"
    & $UninstallServiceScript -ServiceName $ServiceName
    Write-Host "Windows Service removal requested: $ServiceName"
}

if ($RemoveStartup) {
    Remove-StartupService
    exit 0
}

$uvCommand = Get-UvCommand

if (-not $uvCommand) {
    Write-Host "uv is missing."
    Write-Host "Install it with:"
    Write-Host "  $InstallCommandText"
    Write-Host "Or rerun this setup script with:"
    Write-Host "  .\scripts\setup.ps1 -InstallUv"

    if (-not $InstallUv) {
        exit 1
    }

    Write-Host ""
    Write-Host "Installing uv..."
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    Add-UvInstallPaths
    $uvCommand = Get-UvCommand
    if (-not $uvCommand) {
        Write-Error "uv installation completed, but uv was not found on PATH. Restart PowerShell or add uv to PATH, then rerun .\scripts\setup.ps1."
        exit 1
    }
}

Write-Host "Syncing dependencies with uv..."
Invoke-Uv -Arguments @("sync", "--extra", "dev")

Write-Host "Running tests..."
Invoke-ProjectTests

Write-Host ""
$ranTelegramSetup = $false
if ((-not $SkipTelegramSetup) -and (Read-YesNo -Prompt "Run Telegram setup now?" -Default $true)) {
    $ranTelegramSetup = $true
    Invoke-Uv -Arguments @("run", "codex-gateway", "telegram", "setup")
}

$startedGateway = $false
$startupServiceExists = Test-StartupServiceExists
$startupPrompt = if ($startupServiceExists) {
    "Update and restart existing Codex Gateway Windows Service to apply current .env (requires elevated PowerShell)?"
}
else {
    "Install and start Codex Gateway as a Windows Service (requires elevated PowerShell)?"
}
if ((-not $SkipStartup) -and (Read-YesNo -Prompt $startupPrompt -Default $false)) {
    Install-StartupService -Existing $startupServiceExists
    $startedGateway = $true
}

if ($ranTelegramSetup) {
    Write-Host ""
    if ($startedGateway) {
        Write-Host "Codex Gateway can receive Telegram messages now."
        Write-Host "Send /start from the configured Telegram user to get the pairing command."
    }
    else {
        if ($startupServiceExists) {
            Write-Host "Existing Windows Service was not restarted. Run this to apply .env changes:"
            Write-Host "  .\scripts\install-gateway-service.ps1 -Start"
        }
        else {
            Write-Host "Start the gateway, then send /start from the configured Telegram user to get the pairing command:"
            Write-Host "  uv run codex-gateway telegram run"
        }
    }
}

Write-Host ""
Write-Host "Next steps:"
if (-not $ranTelegramSetup) {
    Write-Host "  uv run codex-gateway telegram setup"
}
Write-Host "  uv run codex-gateway telegram status"
if (-not $startedGateway) {
    Write-Host "  .\scripts\install-gateway-service.ps1 -Start"
    if (-not $startupServiceExists) {
        Write-Host "  uv run codex-gateway telegram run"
    }
}
