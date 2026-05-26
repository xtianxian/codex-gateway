from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest

from codex_gateway.backends.codex_app_server.lifecycle import AppServerProcessManager, _fixed_loopback_port


class FakeProcess:
    def __init__(self, *, pid: int | None = None) -> None:
        if pid is not None:
            self.pid = pid
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False

    async def wait(self) -> int:
        return self.returncode or 0

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = 0


@pytest.mark.asyncio
async def test_process_manager_starts_app_server_on_loopback_websocket_and_polls_readyz() -> None:
    process = FakeProcess()
    commands: list[tuple[str, ...]] = []
    ready_urls: list[str] = []

    async def process_factory(*command: str, **_kwargs: Any) -> FakeProcess:
        commands.append(tuple(command))
        return process

    async def ready_checker(url: str) -> bool:
        ready_urls.append(url)
        return len(ready_urls) == 2

    manager = AppServerProcessManager(
        codex_bin="codex",
        url="ws://127.0.0.1:0",
        process_factory=process_factory,
        ready_checker=ready_checker,
        poll_interval_seconds=0,
    )

    await manager.start()

    assert commands[0][0:3] == ("codex", "app-server", "--listen")
    assert commands[0][3].startswith("ws://127.0.0.1:")
    assert commands[0][3] != "ws://127.0.0.1:0"
    assert ready_urls == [
        commands[0][3].replace("ws://", "http://") + "/readyz",
        commands[0][3].replace("ws://", "http://") + "/readyz",
    ]
    assert manager.url == commands[0][3]

    await manager.stop()
    assert process.terminated is True


@pytest.mark.asyncio
async def test_process_manager_stops_windows_process_tree_when_pid_is_available() -> None:
    process = FakeProcess(pid=1234)
    terminated_pids: list[int] = []

    async def process_factory(*_command: str, **_kwargs: Any) -> FakeProcess:
        return process

    async def ready_checker(_url: str) -> bool:
        return True

    async def process_tree_terminator(pid: int) -> bool:
        terminated_pids.append(pid)
        process.returncode = 0
        return True

    manager = AppServerProcessManager(
        codex_bin="codex",
        url="ws://127.0.0.1:0",
        process_factory=process_factory,
        ready_checker=ready_checker,
        process_tree_terminator=process_tree_terminator,
    )

    await manager.start()
    await manager.stop()

    assert terminated_pids == [1234]
    assert process.terminated is False
    assert process.killed is False


@pytest.mark.asyncio
async def test_process_manager_cleans_fixed_loopback_port_before_starting() -> None:
    process = FakeProcess()
    events: list[str] = []

    async def process_factory(*_command: str, **_kwargs: Any) -> FakeProcess:
        events.append("start")
        return process

    async def ready_checker(_url: str) -> bool:
        return True

    async def port_cleanup(url: str) -> None:
        events.append(f"cleanup:{url}")

    manager = AppServerProcessManager(
        codex_bin="codex",
        url="ws://127.0.0.1:8765",
        process_factory=process_factory,
        ready_checker=ready_checker,
        port_cleanup=port_cleanup,
    )

    await manager.start()

    assert events == ["cleanup:ws://127.0.0.1:8765", "start"]


@pytest.mark.asyncio
async def test_process_manager_skips_port_cleanup_for_ephemeral_port() -> None:
    process = FakeProcess()
    cleanup_urls: list[str] = []

    async def process_factory(*_command: str, **_kwargs: Any) -> FakeProcess:
        return process

    async def ready_checker(_url: str) -> bool:
        return True

    async def port_cleanup(url: str) -> None:
        cleanup_urls.append(url)

    manager = AppServerProcessManager(
        codex_bin="codex",
        url="ws://127.0.0.1:0",
        process_factory=process_factory,
        ready_checker=ready_checker,
        port_cleanup=port_cleanup,
    )

    await manager.start()

    assert cleanup_urls == []


def test_fixed_loopback_port_requires_nonzero_loopback_port() -> None:
    assert _fixed_loopback_port("ws://127.0.0.1:8765") == 8765
    assert _fixed_loopback_port("ws://localhost:8765") == 8765
    assert _fixed_loopback_port("ws://127.0.0.1:0") is None
    assert _fixed_loopback_port("ws://192.0.2.1:8765") is None


@pytest.mark.skipif(os.name != "nt", reason="Windows .cmd shim resolution is Windows-only.")
def test_process_manager_resolves_windows_command_shim(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from codex_gateway.backends.codex_app_server.lifecycle import _resolve_executable

    codex_cmd = tmp_path / "codex.cmd"
    codex_cmd.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setenv("PATH", str(tmp_path))

    assert _resolve_executable("codex").lower() == str(codex_cmd).lower()
