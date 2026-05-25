from __future__ import annotations

import asyncio
import os
import shutil
import socket
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse, urlunparse

import httpx


ProcessFactory = Callable[..., Awaitable[Any]]
ReadyChecker = Callable[[str], Awaitable[bool]]
PortCleanup = Callable[[str], Awaitable[None]]
ProcessTreeTerminator = Callable[[int], Awaitable[bool]]


class AppServerProcessManager:
    def __init__(
        self,
        *,
        codex_bin: str,
        url: str,
        process_factory: ProcessFactory | None = None,
        ready_checker: ReadyChecker | None = None,
        port_cleanup: PortCleanup | None = None,
        process_tree_terminator: ProcessTreeTerminator | None = None,
        app_server_args: tuple[str, ...] = (),
        poll_interval_seconds: float = 0.1,
        ready_timeout_seconds: float = 10,
    ) -> None:
        self.codex_bin = codex_bin
        self.url = _resolve_free_port(url)
        self.app_server_args = app_server_args
        self.process_factory = process_factory or _create_process
        self.ready_checker = ready_checker or _readyz
        self.port_cleanup = port_cleanup or _cleanup_stale_app_server
        self.process_tree_terminator = process_tree_terminator or (
            _terminate_windows_process_tree if os.name == "nt" else None
        )
        self.cleanup_port_on_start = _fixed_loopback_port(url) is not None
        self.poll_interval_seconds = poll_interval_seconds
        self.ready_timeout_seconds = ready_timeout_seconds
        self.process: Any | None = None

    async def start(self) -> None:
        if self.cleanup_port_on_start:
            await self.port_cleanup(self.url)
        self.process = await self.process_factory(
            self.codex_bin,
            "app-server",
            *self.app_server_args,
            "--listen",
            self.url,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await self._wait_ready()

    async def stop(self) -> None:
        process = self.process
        self.process = None
        if process is None:
            return
        if getattr(process, "returncode", None) is None:
            tree_terminated = False
            pid = getattr(process, "pid", None)
            if isinstance(pid, int) and self.process_tree_terminator is not None:
                tree_terminated = await self.process_tree_terminator(pid)
            if not tree_terminated and getattr(process, "returncode", None) is None:
                process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()

    async def _wait_ready(self) -> None:
        ready_url = _readyz_url(self.url)
        deadline = asyncio.get_running_loop().time() + self.ready_timeout_seconds
        while True:
            if await self.ready_checker(ready_url):
                return
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError(f"Codex app-server did not become ready at {ready_url}")
            await asyncio.sleep(self.poll_interval_seconds)


async def _create_process(*command: str, **kwargs: Any) -> asyncio.subprocess.Process:
    if command:
        command = (_resolve_executable(command[0]), *command[1:])
    return await asyncio.create_subprocess_exec(*command, **kwargs)


async def _terminate_windows_process_tree(pid: int) -> bool:
    powershell = _resolve_powershell()
    if powershell is None:
        return False
    process = await asyncio.create_subprocess_exec(
        powershell,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        _windows_process_tree_stop_script(pid),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await asyncio.wait_for(process.wait(), timeout=10)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        return False
    return process.returncode == 0


def _windows_process_tree_stop_script(pid: int) -> str:
    return rf"""
$RootProcessId = {pid}
$AllProcesses = Get-Process -ErrorAction SilentlyContinue
$TargetIds = New-Object 'System.Collections.Generic.HashSet[int]'
[void]$TargetIds.Add($RootProcessId)

do {{
    $Added = $false
    foreach ($Process in $AllProcesses) {{
        try {{
            $ParentId = if ($Process.Parent) {{ [int]$Process.Parent.Id }} else {{ 0 }}
        }} catch {{
            $ParentId = 0
        }}
        if ($TargetIds.Contains($ParentId) -and -not $TargetIds.Contains([int]$Process.Id)) {{
            [void]$TargetIds.Add([int]$Process.Id)
            $Added = $true
        }}
    }}
}} while ($Added)

$Targets = $AllProcesses | Where-Object {{ $TargetIds.Contains([int]$_.Id) }} | Sort-Object StartTime -Descending
foreach ($Process in $Targets) {{
    Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
}}

$Deadline = (Get-Date).AddSeconds(5)
while ((Get-Date) -lt $Deadline) {{
    if (-not (Get-Process -Id $RootProcessId -ErrorAction SilentlyContinue)) {{
        exit 0
    }}
    Start-Sleep -Milliseconds 100
}}
exit 1
"""


def _resolve_executable(executable: str) -> str:
    return shutil.which(executable) or executable


async def _readyz(url: str) -> bool:
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=1)
    except httpx.HTTPError:
        return False
    return response.status_code == 200


def _resolve_free_port(url: str) -> str:
    parsed = urlparse(url)
    if parsed.hostname not in {"127.0.0.1", "localhost"} or parsed.port != 0:
        return url
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((parsed.hostname, 0))
        _, port = sock.getsockname()
    netloc = f"{parsed.hostname}:{port}"
    return urlunparse((parsed.scheme, netloc, parsed.path or "", parsed.params, parsed.query, parsed.fragment))


def _readyz_url(websocket_url: str) -> str:
    parsed = urlparse(websocket_url)
    scheme = "https" if parsed.scheme == "wss" else "http"
    path = parsed.path.rstrip("/")
    return urlunparse((scheme, parsed.netloc, f"{path}/readyz", "", "", ""))


def _fixed_loopback_port(url: str) -> int | None:
    parsed = urlparse(url)
    if parsed.hostname not in {"127.0.0.1", "localhost"}:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    if port is None or port == 0:
        return None
    return port


async def _cleanup_stale_app_server(url: str) -> None:
    if os.name != "nt":
        return
    port = _fixed_loopback_port(url)
    if port is None:
        return
    powershell = _resolve_powershell()
    if powershell is None:
        return

    process = await asyncio.create_subprocess_exec(
        powershell,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        _windows_stale_app_server_cleanup_script(port),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await asyncio.wait_for(process.wait(), timeout=8)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()


def _resolve_powershell() -> str | None:
    return shutil.which("pwsh") or shutil.which("powershell")


def _windows_stale_app_server_cleanup_script(port: int) -> str:
    return rf"""
$PortNumber = {port}
$PortPattern = ":{port}(?!\d)"
$TargetIds = @()

function Stop-ProcessTree {{
    param([int]$RootProcessId)
    $AllProcesses = Get-Process -ErrorAction SilentlyContinue
    $TreeIds = New-Object 'System.Collections.Generic.HashSet[int]'
    [void]$TreeIds.Add($RootProcessId)

    do {{
        $Added = $false
        foreach ($RunningProcess in $AllProcesses) {{
            try {{
                $ParentId = if ($RunningProcess.Parent) {{ [int]$RunningProcess.Parent.Id }} else {{ 0 }}
            }} catch {{
                $ParentId = 0
            }}
            if ($TreeIds.Contains($ParentId) -and -not $TreeIds.Contains([int]$RunningProcess.Id)) {{
                [void]$TreeIds.Add([int]$RunningProcess.Id)
                $Added = $true
            }}
        }}
    }} while ($Added)

    $Targets = $AllProcesses | Where-Object {{ $TreeIds.Contains([int]$_.Id) }} | Sort-Object StartTime -Descending
    foreach ($Target in $Targets) {{
        Stop-Process -Id $Target.Id -Force -ErrorAction SilentlyContinue
    }}
}}

$Connections = Get-NetTCPConnection -LocalPort $PortNumber -State Listen -ErrorAction SilentlyContinue
foreach ($Connection in $Connections) {{
    $OwnerId = [int]$Connection.OwningProcess
    if ($OwnerId -le 0) {{
        continue
    }}

    $Process = Get-CimInstance Win32_Process -Filter "ProcessId = $OwnerId" -ErrorAction SilentlyContinue
    if ($null -eq $Process) {{
        continue
    }}

    $CommandLine = [string]$Process.CommandLine
    if ($CommandLine -match "app-server" -and $CommandLine -match "--listen" -and $CommandLine -match $PortPattern) {{
        $TargetIds += $OwnerId

        $AncestorId = [int]$Process.ParentProcessId
        while ($AncestorId -gt 0) {{
            $Ancestor = Get-CimInstance Win32_Process -Filter "ProcessId = $AncestorId" -ErrorAction SilentlyContinue
            if ($null -eq $Ancestor) {{
                break
            }}
            $AncestorCommandLine = [string]$Ancestor.CommandLine
            if (-not ($AncestorCommandLine -match "app-server" -and $AncestorCommandLine -match "--listen" -and $AncestorCommandLine -match $PortPattern)) {{
                break
            }}
            $TargetIds += $AncestorId
            $AncestorId = [int]$Ancestor.ParentProcessId
        }}
    }}
}}

$TargetIds = $TargetIds | Sort-Object -Unique
foreach ($TargetId in $TargetIds) {{
    Stop-ProcessTree -RootProcessId $TargetId
}}

$Deadline = (Get-Date).AddSeconds(5)
while ((Get-Date) -lt $Deadline) {{
    $Remaining = Get-NetTCPConnection -LocalPort $PortNumber -State Listen -ErrorAction SilentlyContinue |
        Where-Object {{ $TargetIds -contains [int]$_.OwningProcess }}
    if (-not $Remaining) {{
        break
    }}
    Start-Sleep -Milliseconds 100
}}
"""
