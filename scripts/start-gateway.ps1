[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$LogDirectory = Join-Path $ProjectRoot ".codex-gateway\logs"
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$LogPath = Join-Path $LogDirectory "telegram-gateway-$Timestamp.log"

New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
Set-Location $ProjectRoot

& uv run codex-gateway telegram run *> $LogPath
exit $LASTEXITCODE
