[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$ServiceName = "CodexGateway",
    [string]$EnvFile = ".env",
    [string]$StateDir,
    [switch]$SkipProcessCleanup,
    [switch]$DockerGatewayVolumes
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ServiceDisplayName = "Codex Gateway"
$DefaultProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ProjectRoot = if (-not [string]::IsNullOrWhiteSpace($env:CODEX_GATEWAY_TEST_PROJECT_ROOT)) {
    [IO.Path]::GetFullPath($env:CODEX_GATEWAY_TEST_PROJECT_ROOT)
}
else {
    $DefaultProjectRoot
}
$ProjectRoot = $ProjectRoot.TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
$UninstallServiceScript = Join-Path $PSScriptRoot "uninstall-gateway-service.ps1"
$ComposeFile = Join-Path $ProjectRoot "testing\docker\compose.linux.yaml"
$DockerGatewayVolumeNames = @("codex-gateway-linux_gateway-config", "codex-gateway-linux_gateway-state")

function Write-Notice {
    param([string]$Message)

    Write-Host $Message
}

function Test-Truthy {
    param([string]$Value)

    if ($null -eq $Value) {
        return $false
    }
    $normalized = $Value.Trim().ToLowerInvariant()
    return $normalized -in @("1", "true", "yes", "y")
}

function Test-IsAdministrator {
    if (Test-Truthy -Value $env:CODEX_GATEWAY_TEST_ASSUME_ADMIN) {
        return $true
    }

    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Test-ServiceExists {
    param([string]$Name)

    if ($null -ne $env:CODEX_GATEWAY_TEST_SERVICE_EXISTS) {
        return (Test-Truthy -Value $env:CODEX_GATEWAY_TEST_SERVICE_EXISTS)
    }

    return $null -ne (Get-Service -Name $Name -ErrorAction SilentlyContinue)
}

function Resolve-TargetPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PathValue,
        [string]$BasePath = $ProjectRoot
    )

    $expanded = [Environment]::ExpandEnvironmentVariables($PathValue).Trim()
    if ([string]::IsNullOrWhiteSpace($expanded)) {
        return $null
    }

    if (-not [IO.Path]::IsPathRooted($expanded)) {
        $expanded = Join-Path $BasePath $expanded
    }

    try {
        return (Resolve-Path -LiteralPath $expanded -ErrorAction Stop).Path.TrimEnd(
            [IO.Path]::DirectorySeparatorChar,
            [IO.Path]::AltDirectorySeparatorChar
        )
    }
    catch {
        return [IO.Path]::GetFullPath($expanded).TrimEnd(
            [IO.Path]::DirectorySeparatorChar,
            [IO.Path]::AltDirectorySeparatorChar
        )
    }
}

function Test-SamePath {
    param([string]$Left, [string]$Right)

    return [string]::Equals($Left, $Right, [StringComparison]::OrdinalIgnoreCase)
}

function Test-AncestorPath {
    param([string]$Ancestor, [string]$Descendant)

    if (Test-SamePath -Left $Ancestor -Right $Descendant) {
        return $true
    }

    $prefix = $Ancestor.TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    return $Descendant.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)
}

function Read-DotEnvFile {
    param([string]$Path)

    $values = @{}
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $values
    }

    foreach ($line in Get-Content -LiteralPath $Path -Encoding utf8) {
        $trimmed = $line.Trim()
        if ([string]::IsNullOrWhiteSpace($trimmed) -or $trimmed.StartsWith("#")) {
            continue
        }
        if ($trimmed.StartsWith("export ")) {
            $trimmed = $trimmed.Substring(7).Trim()
        }

        $index = $trimmed.IndexOf("=")
        if ($index -lt 1) {
            continue
        }

        $key = $trimmed.Substring(0, $index).Trim()
        $value = $trimmed.Substring($index + 1).Trim()
        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        $values[$key] = $value
    }

    return $values
}

function Split-PathList {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return @()
    }

    return @(
        $Value -split "[;,]" |
            ForEach-Object { $_.Trim() } |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
}

function Get-WorkspaceRoots {
    param([hashtable]$EnvValues)

    $roots = [System.Collections.Generic.List[string]]::new()
    foreach ($key in @("CODEX_GATEWAY_ALLOWED_ROOTS", "CODEX_ALLOWED_ROOTS")) {
        if ($EnvValues.ContainsKey($key)) {
            foreach ($part in Split-PathList -Value $EnvValues[$key]) {
                $resolved = Resolve-TargetPath -PathValue $part
                if ($resolved) {
                    $roots.Add($resolved)
                }
            }
        }
    }
    foreach ($key in @("CODEX_GATEWAY_DEFAULT_CWD", "CODEX_DEFAULT_CWD")) {
        if ($EnvValues.ContainsKey($key)) {
            $resolved = Resolve-TargetPath -PathValue $EnvValues[$key]
            if ($resolved) {
                $roots.Add($resolved)
            }
        }
    }

    return @($roots.ToArray() | Select-Object -Unique)
}

function Get-ProfileRoots {
    $roots = [System.Collections.Generic.List[string]]::new()
    foreach ($value in @($env:USERPROFILE, $env:HOME, $env:HOMEDRIVE, $env:LOCALAPPDATA, $env:APPDATA)) {
        if (-not [string]::IsNullOrWhiteSpace($value)) {
            try {
                $roots.Add((Resolve-TargetPath -PathValue $value -BasePath $ProjectRoot))
            }
            catch {
                continue
            }
        }
    }
    if (-not [string]::IsNullOrWhiteSpace($env:USERPROFILE)) {
        $roots.Add((Resolve-TargetPath -PathValue (Join-Path $env:USERPROFILE ".codex") -BasePath $ProjectRoot))
    }
    if (-not [string]::IsNullOrWhiteSpace($env:CODEX_HOME)) {
        $roots.Add((Resolve-TargetPath -PathValue $env:CODEX_HOME -BasePath $ProjectRoot))
    }

    return @($roots.ToArray() | Where-Object { $_ } | Select-Object -Unique)
}

function Assert-SafeDeleteTarget {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FullPath,
        [string[]]$WorkspaceRoots = @()
    )

    $root = [IO.Path]::GetPathRoot($FullPath)
    if (-not [string]::IsNullOrWhiteSpace($root)) {
        $normalizedRoot = $root.TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
        if (Test-SamePath -Left $FullPath -Right $normalizedRoot) {
            throw "Refusing to remove filesystem root: $FullPath"
        }
    }

    if (Test-SamePath -Left $FullPath -Right $ProjectRoot) {
        throw "Refusing to remove the repository root: $FullPath"
    }

    foreach ($profileRoot in Get-ProfileRoots) {
        if (Test-SamePath -Left $FullPath -Right $profileRoot) {
            throw "Refusing to remove profile or Codex auth directory: $FullPath"
        }
    }

    foreach ($workspaceRoot in $WorkspaceRoots) {
        if (Test-SamePath -Left $FullPath -Right $workspaceRoot) {
            throw "Refusing to remove configured workspace root: $FullPath"
        }
        if (Test-AncestorPath -Ancestor $FullPath -Descendant $workspaceRoot) {
            throw "Refusing to remove parent of configured workspace root: $FullPath"
        }
    }

    $leaf = Split-Path -Leaf $FullPath
    if ($leaf -in @("Users", "Documents", "Desktop", "Downloads", "Projects", "src", "repo", "repos", "workspaces", "workspace", "tmp", "temp")) {
        throw "Refusing to remove broad parent directory: $FullPath"
    }
}

function Test-GatewayOwnedStatePath {
    param([string]$FullPath)

    $repoGatewayRoot = Resolve-TargetPath -PathValue ".codex-gateway"
    if (Test-AncestorPath -Ancestor $repoGatewayRoot -Descendant $FullPath) {
        return $true
    }

    $parts = $FullPath -split "[\\/]+"
    foreach ($part in $parts) {
        if ($part -in @(".codex-gateway", "codex-gateway")) {
            return $true
        }
    }

    return $false
}

function Remove-Target {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FullPath,
        [Parameter(Mandatory = $true)]
        [string]$Description,
        [string[]]$WorkspaceRoots = @()
    )

    Assert-SafeDeleteTarget -FullPath $FullPath -WorkspaceRoots $WorkspaceRoots

    if (-not (Test-Path -LiteralPath $FullPath)) {
        Write-Notice "Already absent: $Description ($FullPath)"
        return
    }

    if ($PSCmdlet.ShouldProcess($FullPath, "Remove $Description")) {
        Remove-Item -LiteralPath $FullPath -Recurse -Force
        Write-Notice "Removed: $Description ($FullPath)"
    }
}

function Get-ConfiguredStateDirs {
    param(
        [hashtable]$EnvValues,
        [string]$ExplicitStateDir
    )

    $entries = [System.Collections.Generic.List[object]]::new()
    if (-not [string]::IsNullOrWhiteSpace($ExplicitStateDir)) {
        $entries.Add([pscustomobject]@{
            Path = $ExplicitStateDir
            Explicit = $true
        })
    }
    foreach ($key in @("CODEX_GATEWAY_TELEGRAM_STATE_DIR", "CODEX_TELEGRAM_STATE_DIR")) {
        if ($EnvValues.ContainsKey($key) -and -not [string]::IsNullOrWhiteSpace($EnvValues[$key])) {
            $entries.Add([pscustomobject]@{
                Path = $EnvValues[$key]
                Explicit = $false
            })
        }
    }

    return $entries
}

function Invoke-ServiceCleanup {
    param([string]$Name)

    if (Test-ServiceExists -Name $Name) {
        if (-not (Test-IsAdministrator)) {
            throw "Removing the Windows Service and deleting gateway files requires an elevated PowerShell because service '$Name' exists."
        }

        if ($PSCmdlet.ShouldProcess($Name, "Remove Windows Service")) {
            & $UninstallServiceScript -ServiceName $Name
            if (-not $?) {
                throw "Windows Service uninstall script failed for '$Name'."
            }
        }
    }
    else {
        Write-Notice "No Windows Service found: $Name"
    }
}

function Invoke-ProcessCleanup {
    if ($SkipProcessCleanup) {
        Write-Notice "Skipped process cleanup."
        return
    }

    if ($env:CODEX_GATEWAY_TEST_PROCESS_LOG) {
        "process cleanup ProjectRoot=$ProjectRoot" |
            Out-File -FilePath $env:CODEX_GATEWAY_TEST_PROCESS_LOG -Encoding utf8 -Append
        return
    }

    $serviceRoot = Resolve-TargetPath -PathValue ".codex-gateway\service"
    $matches = @()
    foreach ($process in Get-Process -ErrorAction SilentlyContinue) {
        if ($PID -eq $process.Id) {
            continue
        }

        $path = $null
        try {
            $path = $process.Path
        }
        catch {
            $path = $null
        }
        if ([string]::IsNullOrWhiteSpace($path)) {
            continue
        }

        $fullPath = Resolve-TargetPath -PathValue $path
        if (Test-AncestorPath -Ancestor $serviceRoot -Descendant $fullPath) {
            $matches += $process
        }
    }

    if ($matches.Count -eq 0) {
        Write-Notice "No matching gateway wrapper processes found."
        Write-Notice "Skipped command-line process matching to avoid known Windows CIM/WMI hangs."
        return
    }

    foreach ($match in $matches) {
        if ($PSCmdlet.ShouldProcess("PID $($match.Id)", "Stop matching Codex Gateway process")) {
            Stop-Process -Id $match.Id -Force -ErrorAction SilentlyContinue
            Write-Notice "Stopped matching gateway wrapper process: PID $($match.Id)"
        }
    }
}

function Invoke-DockerCommand {
    param([string[]]$Arguments)

    $line = "docker " + ($Arguments -join " ")
    if ($env:CODEX_GATEWAY_TEST_DOCKER_LOG) {
        $line | Out-File -FilePath $env:CODEX_GATEWAY_TEST_DOCKER_LOG -Encoding utf8 -Append
        return
    }

    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $docker) {
        Write-Warning "Docker was not found on PATH; skipped: $line"
        return
    }

    & docker @Arguments
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Docker command failed: $line"
    }
}

function Invoke-DockerGatewayVolumeCleanup {
    if (-not $DockerGatewayVolumes) {
        return
    }

    $downArgs = @("compose", "-f", $ComposeFile, "down", "--remove-orphans")
    if ($PSCmdlet.ShouldProcess($ComposeFile, "Stop Docker Compose gateway stack")) {
        Invoke-DockerCommand -Arguments $downArgs
    }

    $volumeArgs = @("volume", "rm") + $DockerGatewayVolumeNames
    if ($PSCmdlet.ShouldProcess(($DockerGatewayVolumeNames -join ", "), "Remove Docker gateway config/state volumes")) {
        Invoke-DockerCommand -Arguments $volumeArgs
    }
}

$resolvedEnvFile = Resolve-TargetPath -PathValue $EnvFile
$envValues = Read-DotEnvFile -Path $resolvedEnvFile
$workspaceRoots = Get-WorkspaceRoots -EnvValues $envValues
$repoLocalState = Resolve-TargetPath -PathValue ".codex-gateway"
$stateTargets = [System.Collections.Generic.List[string]]::new()
$seenStateDirs = @{}

Assert-SafeDeleteTarget -FullPath $resolvedEnvFile -WorkspaceRoots $workspaceRoots
Assert-SafeDeleteTarget -FullPath $repoLocalState -WorkspaceRoots $workspaceRoots
foreach ($entry in Get-ConfiguredStateDirs -EnvValues $envValues -ExplicitStateDir $StateDir) {
    $resolvedStateDir = Resolve-TargetPath -PathValue $entry.Path
    if (-not $resolvedStateDir) {
        continue
    }
    $key = $resolvedStateDir.ToLowerInvariant()
    if ($seenStateDirs.ContainsKey($key)) {
        continue
    }
    $seenStateDirs[$key] = $true

    if ((-not $entry.Explicit) -and (-not (Test-GatewayOwnedStatePath -FullPath $resolvedStateDir))) {
        Write-Warning "Skipped configured Telegram state dir because it is not clearly gateway-owned. Pass -StateDir to remove it explicitly: $resolvedStateDir"
        continue
    }

    Assert-SafeDeleteTarget -FullPath $resolvedStateDir -WorkspaceRoots $workspaceRoots
    $stateTargets.Add($resolvedStateDir)
}

Write-Notice "Codex Gateway full uninstall removes gateway config, state, startup integration, and logs only."
Write-Notice "Codex CLI login/auth, workspaces, Docker codex-home, and Telegram BotFather token state are preserved."

Invoke-ServiceCleanup -Name $ServiceName
Invoke-ProcessCleanup

Remove-Target -FullPath $resolvedEnvFile -Description "environment file" -WorkspaceRoots $workspaceRoots

Remove-Target -FullPath $repoLocalState -Description "repo-local gateway state/logs" -WorkspaceRoots $workspaceRoots

foreach ($target in $stateTargets) {
    Remove-Target -FullPath $target -Description "Telegram state directory" -WorkspaceRoots $workspaceRoots
}

Invoke-DockerGatewayVolumeCleanup

Write-Notice "Manual follow-up: revoke or rotate the Telegram bot token through BotFather only if you want the bot token invalidated."
Write-Notice "Codex CLI login/auth was not removed."
