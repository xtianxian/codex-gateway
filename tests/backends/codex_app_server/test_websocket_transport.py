from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from codex_gateway.backends.codex_app_server.client import AppServerClient, JsonRpcError
from codex_gateway.backends.codex_app_server.transport import WebSocketJsonRpcTransport


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.incoming: asyncio.Queue[str | None] = asyncio.Queue()
        self.closed = False

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str:
        message = await self.incoming.get()
        if message is None:
            raise ConnectionError("closed")
        return message

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_websocket_transport_sends_and_receives_json_messages() -> None:
    socket = FakeWebSocket()
    connect_kwargs: dict[str, Any] = {}

    async def connect(url: str, **kwargs: Any) -> FakeWebSocket:
        assert url == "ws://127.0.0.1:8765"
        connect_kwargs.update(kwargs)
        return socket

    transport = WebSocketJsonRpcTransport("ws://127.0.0.1:8765", connect_fn=connect)
    await transport.start()

    await transport.send({"jsonrpc": "2.0", "method": "ping"})
    await socket.incoming.put(json.dumps({"jsonrpc": "2.0", "result": "pong", "id": 1}))

    assert json.loads(socket.sent[0]) == {"jsonrpc": "2.0", "method": "ping"}
    assert await transport.receive() == {"jsonrpc": "2.0", "result": "pong", "id": 1}
    assert connect_kwargs["max_size"] == 64 * 1024 * 1024

    await transport.close()
    assert socket.closed is True


@pytest.mark.asyncio
async def test_initialize_then_initialized_sequence_uses_gateway_client_identity() -> None:
    class FakeTransport:
        def __init__(self) -> None:
            self.sent: list[dict[str, Any]] = []
            self.incoming: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        async def send(self, message: dict[str, Any]) -> None:
            self.sent.append(message)

        async def receive(self) -> dict[str, Any] | None:
            return await self.incoming.get()

        async def close(self) -> None:
            pass

    transport = FakeTransport()
    client = AppServerClient(transport=transport)
    await client.start_reader()

    pending = asyncio.create_task(client.initialize())
    await asyncio.sleep(0)
    await transport.incoming.put({"id": 1, "result": {}})
    await pending

    assert transport.sent[0]["method"] == "initialize"
    assert transport.sent[0]["params"]["clientInfo"]["name"] == "codex_gateway"
    assert transport.sent[0]["params"]["capabilities"] == {"experimentalApi": True}
    assert transport.sent[1] == {"jsonrpc": "2.0", "method": "initialized", "params": {}}

    await client.stop()


@pytest.mark.asyncio
async def test_server_overloaded_error_is_retried_once_for_safe_request() -> None:
    class FakeTransport:
        def __init__(self) -> None:
            self.sent: list[dict[str, Any]] = []
            self.incoming: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        async def send(self, message: dict[str, Any]) -> None:
            self.sent.append(message)

        async def receive(self) -> dict[str, Any] | None:
            return await self.incoming.get()

        async def close(self) -> None:
            pass

    transport = FakeTransport()
    client = AppServerClient(transport=transport, retry_delay_seconds=0)
    await client.start_reader()

    pending = asyncio.create_task(client.request("model/list", {}))
    await asyncio.sleep(0)
    await transport.incoming.put({"id": 1, "error": {"code": -32001, "message": "Server overloaded"}})
    await asyncio.sleep(0)
    await transport.incoming.put({"id": 2, "result": {"data": []}})

    assert await pending == {"data": []}
    assert [message["id"] for message in transport.sent] == [1, 2]

    await client.stop()


@pytest.mark.asyncio
async def test_turn_start_disconnect_is_not_retried_blindly() -> None:
    class FakeTransport:
        def __init__(self) -> None:
            self.sent: list[dict[str, Any]] = []
            self.incoming: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        async def send(self, message: dict[str, Any]) -> None:
            self.sent.append(message)

        async def receive(self) -> dict[str, Any] | None:
            return await self.incoming.get()

        async def close(self) -> None:
            pass

    transport = FakeTransport()
    client = AppServerClient(transport=transport)
    await client.start_reader()

    pending = asyncio.create_task(client.turn_start(thread_id="thr_1", input_items=[{"type": "text", "text": "hi"}]))
    await asyncio.sleep(0)
    await transport.incoming.put(None)

    with pytest.raises(JsonRpcError, match="closed"):
        await pending
    assert [message["method"] for message in transport.sent] == ["turn/start"]

    await client.stop()

