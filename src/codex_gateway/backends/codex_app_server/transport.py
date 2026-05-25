from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
from typing import Any, Awaitable, Callable


ConnectFn = Callable[..., Awaitable[Any]]
DEFAULT_WEBSOCKET_MAX_MESSAGE_BYTES = 64 * 1024 * 1024


class TransportClosed(RuntimeError):
    pass


class WebSocketJsonRpcTransport:
    def __init__(
        self,
        url: str,
        *,
        connect_fn: ConnectFn | None = None,
        open_timeout_seconds: float = 10,
        max_message_bytes: int | None = DEFAULT_WEBSOCKET_MAX_MESSAGE_BYTES,
    ) -> None:
        self.url = url
        self.connect_fn = connect_fn or _websocket_connect
        self.open_timeout_seconds = open_timeout_seconds
        self.max_message_bytes = max_message_bytes
        self.connection: Any | None = None

    async def start(self) -> None:
        self.connection = await self.connect_fn(
            self.url,
            open_timeout=self.open_timeout_seconds,
            max_size=self.max_message_bytes,
        )

    async def send(self, message: dict[str, Any]) -> None:
        if self.connection is None:
            raise TransportClosed("WebSocket transport is not connected")
        payload = json.dumps(message, separators=(",", ":"))
        await self.connection.send(payload)

    async def receive(self) -> dict[str, Any] | None:
        if self.connection is None:
            return None
        try:
            payload = await self.connection.recv()
        except Exception as exc:
            if _is_connection_closed(exc):
                return None
            raise
        if payload is None:
            return None
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        return json.loads(str(payload))

    async def close(self) -> None:
        connection = self.connection
        self.connection = None
        if connection is not None:
            await connection.close()


class StdioJsonRpcTransport:
    def __init__(self, command: tuple[str, ...]) -> None:
        self.command = _resolve_command_executable(command)
        self.process: asyncio.subprocess.Process | None = None

    async def start(self) -> None:
        self.process = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )

    async def send(self, message: dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None:
            raise TransportClosed("App-server process is not running")
        line = json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n"
        self.process.stdin.write(line)
        await self.process.stdin.drain()

    async def receive(self) -> dict[str, Any] | None:
        if self.process is None or self.process.stdout is None:
            return None
        line = await self.process.stdout.readline()
        if not line:
            return None
        return json.loads(line.decode("utf-8"))

    async def close(self) -> None:
        process = self.process
        self.process = None
        if process is None:
            return
        if process.stdin is not None:
            process.stdin.close()
            try:
                await asyncio.wait_for(process.stdin.wait_closed(), timeout=2)
            except (asyncio.TimeoutError, OSError, BrokenPipeError):
                pass
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except asyncio.TimeoutError:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=2)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        await asyncio.sleep(0)


async def _websocket_connect(url: str, **kwargs: Any) -> Any:
    try:
        from websockets.asyncio.client import connect
    except ImportError:
        from websockets import connect  # type: ignore[no-redef]

    return await connect(url, **kwargs)


def _is_connection_closed(exc: Exception) -> bool:
    if isinstance(exc, (ConnectionError, EOFError, OSError)):
        return True
    return exc.__class__.__name__.startswith("ConnectionClosed")


def _normalize_command(command: str | tuple[str, ...] | None) -> tuple[str, ...] | None:
    if command is None:
        return None
    if isinstance(command, tuple):
        return command
    return tuple(shlex.split(command, posix=os.name != "nt"))


def _resolve_command_executable(command: tuple[str, ...]) -> tuple[str, ...]:
    if not command:
        return command
    resolved = shutil.which(command[0])
    if resolved is None:
        return command
    return (resolved, *command[1:])
