from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from codex_gateway.backends.codex_app_server.client import (
    AppServerClient,
    AppServerEvent,
    JsonRpcError,
    _resolve_command_executable,
)


class FakeTransport:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.incoming: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self.closed = False

    async def send(self, message: dict[str, Any]) -> None:
        self.sent.append(message)

    async def receive(self) -> dict[str, Any] | None:
        return await self.incoming.get()

    async def close(self) -> None:
        self.closed = True


async def _complete(
    transport: FakeTransport,
    task: asyncio.Task[Any],
    request_id: int,
    result: dict[str, Any] | None = None,
) -> Any:
    await asyncio.sleep(0)
    await transport.incoming.put({"id": request_id, "result": result or {}})
    return await task


def test_windows_command_resolution_uses_cmd_shim(monkeypatch, tmp_path: Path) -> None:
    codex_cmd = tmp_path / "codex.cmd"
    codex_cmd.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setenv("PATH", str(tmp_path))

    resolved = _resolve_command_executable(("codex", "app-server"))

    assert resolved[0].lower() == str(codex_cmd).lower()
    assert resolved[1:] == ("app-server",)


def test_policy_and_sandbox_normalizers_map_cli_style_values() -> None:
    from codex_gateway.gateways.telegram.bridge import _approval_policy_value, _thread_sandbox_value

    assert _approval_policy_value("unlessTrusted") == "on-request"
    assert _approval_policy_value("on-failure") == "on-failure"
    assert _thread_sandbox_value("workspace-write") == "workspace-write"
    assert _thread_sandbox_value("workspaceWrite") == "workspace-write"


@pytest.mark.asyncio
async def test_request_id_generation_and_response_matching() -> None:
    transport = FakeTransport()
    client = AppServerClient(transport=transport)
    await client.start_reader()

    pending = asyncio.create_task(client.request("model/list", {"includeHidden": True}))
    await asyncio.sleep(0)
    assert transport.sent[0]["id"] == 1
    assert transport.sent[0]["method"] == "model/list"

    await transport.incoming.put({"id": 1, "result": {"data": []}})

    assert await pending == {"data": []}

    await client.stop()


@pytest.mark.asyncio
async def test_json_rpc_error_response_raises() -> None:
    transport = FakeTransport()
    client = AppServerClient(transport=transport)
    await client.start_reader()

    pending = asyncio.create_task(client.request("bad", {}))
    await asyncio.sleep(0)
    await transport.incoming.put({"id": 1, "error": {"code": -32000, "message": "nope"}})

    with pytest.raises(JsonRpcError, match="nope"):
        await pending

    await client.stop()


@pytest.mark.asyncio
async def test_notifications_and_server_requests_are_dispatched() -> None:
    notifications: list[AppServerEvent] = []
    requests: list[AppServerEvent] = []
    transport = FakeTransport()
    client = AppServerClient(
        transport=transport,
        on_notification=notifications.append,
        on_request=requests.append,
    )
    await client.start_reader()

    await transport.incoming.put({"method": "turn/completed", "params": {"turn": {"id": "t1"}}})
    await transport.incoming.put({"id": 99, "method": "item/tool/call", "params": {"tool": "telegram_reply"}})
    await asyncio.sleep(0)

    assert notifications[0].method == "turn/completed"
    assert requests[0].request_id == 99
    assert requests[0].method == "item/tool/call"

    await client.stop()


@pytest.mark.asyncio
async def test_transport_close_fails_pending_requests() -> None:
    transport = FakeTransport()
    client = AppServerClient(transport=transport)
    await client.start_reader()

    pending = asyncio.create_task(client.request("model/list", {}))
    await asyncio.sleep(0)
    await transport.incoming.put(None)

    with pytest.raises(JsonRpcError, match="closed"):
        await pending

    await client.stop()


@pytest.mark.asyncio
async def test_initialize_payload_opts_into_experimental_api() -> None:
    transport = FakeTransport()
    client = AppServerClient(transport=transport)
    await client.start_reader()

    pending = asyncio.create_task(client.initialize())
    await asyncio.sleep(0)
    await transport.incoming.put({"id": 1, "result": {}})
    await pending

    assert transport.sent[0] == {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
                "clientInfo": {
                    "name": "codex_gateway",
                    "title": "Codex Gateway",
                    "version": "0.1.0",
                },
            "capabilities": {"experimentalApi": True},
        },
    }
    assert transport.sent[1] == {"jsonrpc": "2.0", "method": "initialized", "params": {}}

    await client.stop()


@pytest.mark.asyncio
async def test_thread_turn_and_response_serialization() -> None:
    transport = FakeTransport()
    client = AppServerClient(transport=transport)
    await client.start_reader()

    thread_task = asyncio.create_task(
        client.thread_start(
            cwd=r"E:\Projects\codex-gateway",
            model="gpt-5.4",
            approval_policy="on-request",
            sandbox="workspaceWrite",
            dynamic_tools=[{"name": "telegram_reply"}],
            developer_instructions="Telegram gateway instructions.",
        )
    )
    await asyncio.sleep(0)
    await transport.incoming.put({"id": 1, "result": {"thread": {"id": "thr_1"}}})
    assert (await thread_task)["thread"]["id"] == "thr_1"
    assert transport.sent[0]["params"]["dynamicTools"] == [{"name": "telegram_reply"}]
    assert transport.sent[0]["params"]["sandbox"] == "workspaceWrite"
    assert transport.sent[0]["params"]["developerInstructions"] == "Telegram gateway instructions."
    assert "sandboxPolicy" not in transport.sent[0]["params"]

    turn_task = asyncio.create_task(
        client.turn_start(
            thread_id="thr_1",
            input_items=[{"type": "text", "text": "Hi"}],
            cwd=r"E:\Projects\codex-gateway",
            approval_policy="on-request",
            sandbox_policy={"type": "workspaceWrite", "writableRoots": [r"E:\Projects\codex-gateway"]},
        )
    )
    await asyncio.sleep(0)
    await transport.incoming.put({"id": 2, "result": {"turn": {"id": "turn_1"}}})
    assert (await turn_task)["turn"]["id"] == "turn_1"
    assert transport.sent[1]["method"] == "turn/start"
    assert "developerInstructions" not in transport.sent[1]["params"]

    await client.send_approval_decision(77, "accept")
    await client.send_dynamic_tool_result(78, [{"type": "text", "text": "sent"}])
    await client.send_mcp_elicitation_response(79, "decline")
    await client.send_permissions_approval_response(
        80,
        {"fileSystem": {"read": [r"E:\Projects\codex-gateway"]}, "network": {"enabled": True}},
    )
    await client.send_tool_user_input_response(81, {"choice": ["Use GPT-5.1"]})

    assert transport.sent[2] == {"jsonrpc": "2.0", "id": 77, "result": {"decision": "accept"}}
    assert transport.sent[3] == {
        "jsonrpc": "2.0",
        "id": 78,
        "result": {
            "contentItems": [{"type": "inputText", "text": "sent"}],
            "success": True,
        },
    }
    assert transport.sent[4] == {"jsonrpc": "2.0", "id": 79, "result": {"action": "decline"}}
    assert transport.sent[5] == {
        "jsonrpc": "2.0",
        "id": 80,
        "result": {
            "permissions": {"fileSystem": {"read": [r"E:\Projects\codex-gateway"]}, "network": {"enabled": True}},
            "scope": "turn",
        },
    }
    assert transport.sent[6] == {
        "jsonrpc": "2.0",
        "id": 81,
        "result": {"answers": {"choice": {"answers": ["Use GPT-5.1"]}}},
    }

    await client.stop()


@pytest.mark.asyncio
async def test_expanded_app_server_wrappers_use_schema_method_names() -> None:
    transport = FakeTransport()
    client = AppServerClient(transport=transport)
    await client.start_reader()

    settings_task = asyncio.create_task(
        client.thread_settings_update(
            thread_id="thr_1",
            model="gpt-5.1",
            approval_policy="never",
            permissions=None,
            effort="high",
        )
    )
    await _complete(transport, settings_task, 1)
    assert transport.sent[0] == {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "thread/settings/update",
        "params": {
            "threadId": "thr_1",
            "model": "gpt-5.1",
            "approvalPolicy": "never",
            "permissions": None,
            "effort": "high",
        },
    }

    name_task = asyncio.create_task(client.thread_set_name(thread_id="thr_1", name="Gateway work"))
    await _complete(transport, name_task, 2)
    assert transport.sent[1]["method"] == "thread/name/set"
    assert transport.sent[1]["params"] == {"threadId": "thr_1", "name": "Gateway work"}

    goal_set_task = asyncio.create_task(
        client.thread_goal_set(thread_id="thr_1", objective="Ship Telegram commands", status="active")
    )
    await _complete(transport, goal_set_task, 3, {"goal": {"objective": "Ship Telegram commands"}})
    assert transport.sent[2]["method"] == "thread/goal/set"
    assert transport.sent[2]["params"] == {
        "threadId": "thr_1",
        "objective": "Ship Telegram commands",
        "status": "active",
    }

    goal_get_task = asyncio.create_task(client.thread_goal_get(thread_id="thr_1"))
    await _complete(transport, goal_get_task, 4)
    assert transport.sent[3]["method"] == "thread/goal/get"

    goal_clear_task = asyncio.create_task(client.thread_goal_clear(thread_id="thr_1"))
    await _complete(transport, goal_clear_task, 5)
    assert transport.sent[4]["method"] == "thread/goal/clear"

    thread_list_task = asyncio.create_task(
        client.thread_list(cwd=r"E:\Projects\repo", search_term="gateway", limit=5)
    )
    await _complete(transport, thread_list_task, 6, {"data": []})
    assert transport.sent[5]["method"] == "thread/list"
    assert transport.sent[5]["params"] == {
        "cwd": r"E:\Projects\repo",
        "searchTerm": "gateway",
        "limit": 5,
    }

    thread_read_task = asyncio.create_task(client.thread_read(thread_id="thr_1", include_turns=True))
    await _complete(transport, thread_read_task, 7, {"thread": {"id": "thr_1"}})
    assert transport.sent[6]["method"] == "thread/read"
    assert transport.sent[6]["params"] == {"threadId": "thr_1", "includeTurns": True}

    interrupt_task = asyncio.create_task(client.turn_interrupt(thread_id="thr_1", turn_id="turn_1"))
    await _complete(transport, interrupt_task, 8)
    assert transport.sent[7]["method"] == "turn/interrupt"
    assert transport.sent[7]["params"] == {"threadId": "thr_1", "turnId": "turn_1"}

    steer_task = asyncio.create_task(
        client.turn_steer(
            thread_id="thr_1",
            expected_turn_id="turn_1",
            input_items=[{"type": "text", "text": "keep going"}],
        )
    )
    await _complete(transport, steer_task, 9)
    assert transport.sent[8]["method"] == "turn/steer"
    assert transport.sent[8]["params"] == {
        "threadId": "thr_1",
        "expectedTurnId": "turn_1",
        "input": [{"type": "text", "text": "keep going"}],
    }

    permission_task = asyncio.create_task(client.permission_profile_list(cwd=r"E:\Projects\repo", limit=20))
    await _complete(transport, permission_task, 10, {"data": []})
    assert transport.sent[9]["method"] == "permissionProfile/list"
    assert transport.sent[9]["params"] == {"cwd": r"E:\Projects\repo", "limit": 20}

    account_task = asyncio.create_task(client.account_read(refresh_token=True))
    await _complete(transport, account_task, 11, {"requiresOpenaiAuth": False})
    assert transport.sent[10]["method"] == "account/read"
    assert transport.sent[10]["params"] == {"refreshToken": True}

    limits_task = asyncio.create_task(client.account_rate_limits_read())
    await _complete(transport, limits_task, 12, {"rateLimits": {}})
    assert transport.sent[11]["method"] == "account/rateLimits/read"
    assert transport.sent[11]["params"] == {}

    mcp_task = asyncio.create_task(client.mcp_server_status_list(detail="toolsAndAuthOnly", limit=10))
    await _complete(transport, mcp_task, 13, {"data": []})
    assert transport.sent[12]["method"] == "mcpServerStatus/list"
    assert transport.sent[12]["params"] == {"detail": "toolsAndAuthOnly", "limit": 10}

    reload_task = asyncio.create_task(client.config_mcp_server_reload())
    await _complete(transport, reload_task, 14)
    assert transport.sent[13]["method"] == "config/mcpServer/reload"
    assert transport.sent[13]["params"] == {}

    hooks_task = asyncio.create_task(client.hooks_list(cwds=[r"E:\Projects\repo"]))
    await _complete(transport, hooks_task, 15, {"data": []})
    assert transport.sent[14]["method"] == "hooks/list"
    assert transport.sent[14]["params"] == {"cwds": [r"E:\Projects\repo"]}

    apps_task = asyncio.create_task(client.app_list(thread_id="thr_1", force_refetch=True))
    await _complete(transport, apps_task, 16, {"data": []})
    assert transport.sent[15]["method"] == "app/list"
    assert transport.sent[15]["params"] == {"threadId": "thr_1", "forceRefetch": True}

    model_task = asyncio.create_task(client.model_list(include_hidden=True, limit=10))
    await _complete(transport, model_task, 17, {"data": []})
    assert transport.sent[16]["method"] == "model/list"
    assert transport.sent[16]["params"] == {"includeHidden": True, "limit": 10}

    features_task = asyncio.create_task(client.experimental_feature_list(thread_id="thr_1", limit=10))
    await _complete(transport, features_task, 18, {"data": []})
    assert transport.sent[17]["method"] == "experimentalFeature/list"
    assert transport.sent[17]["params"] == {"threadId": "thr_1", "limit": 10}

    modes_task = asyncio.create_task(client.collaboration_mode_list())
    await _complete(transport, modes_task, 19, {"data": []})
    assert transport.sent[18]["method"] == "collaborationMode/list"
    assert transport.sent[18]["params"] == {}

    config_task = asyncio.create_task(client.config_read(cwd=r"E:\Projects\repo", include_layers=True))
    await _complete(transport, config_task, 20, {"config": {}, "origins": {}})
    assert transport.sent[19]["method"] == "config/read"
    assert transport.sent[19]["params"] == {"cwd": r"E:\Projects\repo", "includeLayers": True}

    await client.stop()


@pytest.mark.asyncio
async def test_cli_parity_app_server_wrappers_use_schema_method_names() -> None:
    transport = FakeTransport()
    client = AppServerClient(transport=transport)
    await client.start_reader()

    clean_task = asyncio.create_task(client.thread_background_terminals_clean(thread_id="thr_1"))
    await _complete(transport, clean_task, 1)
    assert transport.sent[0] == {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "thread/backgroundTerminals/clean",
        "params": {"threadId": "thr_1"},
    }

    loaded_task = asyncio.create_task(client.thread_loaded_list(limit=10))
    await _complete(transport, loaded_task, 2, {"data": ["thr_1"]})
    assert transport.sent[1]["method"] == "thread/loaded/list"
    assert transport.sent[1]["params"] == {"limit": 10}

    guardian_task = asyncio.create_task(
        client.thread_approve_guardian_denied_action(thread_id="thr_1", event={"reviewId": "rev_1"})
    )
    await _complete(transport, guardian_task, 3)
    assert transport.sent[2]["method"] == "thread/approveGuardianDeniedAction"
    assert transport.sent[2]["params"] == {"threadId": "thr_1", "event": {"reviewId": "rev_1"}}

    features_task = asyncio.create_task(client.experimental_feature_enablement_set(enablement={"web_search": False}))
    await _complete(transport, features_task, 4)
    assert transport.sent[3]["method"] == "experimentalFeature/enablement/set"
    assert transport.sent[3]["params"] == {"enablement": {"web_search": False}}

    skill_task = asyncio.create_task(client.skills_config_write(enabled=False, name="imagegen", path=r"C:\skill.md"))
    await _complete(transport, skill_task, 5)
    assert transport.sent[4]["method"] == "skills/config/write"
    assert transport.sent[4]["params"] == {"enabled": False, "name": "imagegen", "path": r"C:\skill.md"}

    plugin_task = asyncio.create_task(client.plugin_list(cwds=[r"E:\Projects\repo"], marketplace_kinds=["local"]))
    await _complete(transport, plugin_task, 6, {"marketplaces": []})
    assert transport.sent[5]["method"] == "plugin/list"
    assert transport.sent[5]["params"] == {"cwds": [r"E:\Projects\repo"], "marketplaceKinds": ["local"]}

    memory_task = asyncio.create_task(client.thread_memory_mode_set(thread_id="thr_1", mode="disabled"))
    await _complete(transport, memory_task, 7)
    assert transport.sent[6]["method"] == "thread/memoryMode/set"
    assert transport.sent[6]["params"] == {"threadId": "thr_1", "mode": "disabled"}

    rollback_task = asyncio.create_task(client.thread_rollback(thread_id="thr_1", num_turns=2))
    await _complete(transport, rollback_task, 8)
    assert transport.sent[7]["method"] == "thread/rollback"
    assert transport.sent[7]["params"] == {"threadId": "thr_1", "numTurns": 2}

    items_task = asyncio.create_task(
        client.thread_turns_items_list(thread_id="thr_1", turn_id="turn_1", sort_direction="desc", limit=5)
    )
    await _complete(transport, items_task, 9, {"data": []})
    assert transport.sent[8]["method"] == "thread/turns/items/list"
    assert transport.sent[8]["params"] == {
        "threadId": "thr_1",
        "turnId": "turn_1",
        "sortDirection": "desc",
        "limit": 5,
    }

    personality_task = asyncio.create_task(client.thread_settings_update(thread_id="thr_1", personality="pragmatic"))
    await _complete(transport, personality_task, 10)
    assert transport.sent[9]["method"] == "thread/settings/update"
    assert transport.sent[9]["params"] == {"threadId": "thr_1", "personality": "pragmatic"}

    await client.stop()


@pytest.mark.asyncio
async def test_resume_fork_compact_and_skills_list_serialization() -> None:
    transport = FakeTransport()
    client = AppServerClient(transport=transport)
    await client.start_reader()

    resume_task = asyncio.create_task(
        client.thread_resume(
            thread_id="thr_1",
            cwd=r"E:\Projects\repo",
            approval_policy="on-request",
            sandbox="workspaceWrite",
            developer_instructions="Telegram gateway instructions.",
        )
    )
    await asyncio.sleep(0)
    await transport.incoming.put({"id": 1, "result": {"thread": {"id": "thr_1"}}})
    await resume_task
    assert transport.sent[0]["method"] == "thread/resume"
    assert transport.sent[0]["params"]["threadId"] == "thr_1"
    assert transport.sent[0]["params"]["developerInstructions"] == "Telegram gateway instructions."

    fork_task = asyncio.create_task(
        client.thread_fork(
            thread_id="thr_1",
            exclude_turns=True,
            developer_instructions="Telegram gateway instructions.",
        )
    )
    await asyncio.sleep(0)
    await transport.incoming.put({"id": 2, "result": {"thread": {"id": "thr_2"}}})
    await fork_task
    assert transport.sent[1]["method"] == "thread/fork"
    assert transport.sent[1]["params"]["developerInstructions"] == "Telegram gateway instructions."

    compact_task = asyncio.create_task(client.thread_compact_start(thread_id="thr_2"))
    await asyncio.sleep(0)
    await transport.incoming.put({"id": 3, "result": {}})
    await compact_task
    assert transport.sent[2]["method"] == "thread/compact/start"

    archive_task = asyncio.create_task(client.thread_archive(thread_id="thr_2"))
    await asyncio.sleep(0)
    await transport.incoming.put({"id": 4, "result": {}})
    await archive_task
    assert transport.sent[3]["method"] == "thread/archive"

    unarchive_task = asyncio.create_task(client.thread_unarchive(thread_id="thr_2"))
    await asyncio.sleep(0)
    await transport.incoming.put({"id": 5, "result": {}})
    await unarchive_task
    assert transport.sent[4]["method"] == "thread/unarchive"

    skills_task = asyncio.create_task(client.skills_list(cwds=[r"E:\Projects\repo"], force_reload=True))
    await asyncio.sleep(0)
    await transport.incoming.put({"id": 6, "result": {"data": []}})
    await skills_task
    assert transport.sent[5] == {
        "jsonrpc": "2.0",
        "id": 6,
        "method": "skills/list",
        "params": {"cwds": [r"E:\Projects\repo"], "forceReload": True},
    }

    review_task = asyncio.create_task(client.review_start(thread_id="thr_2", target={"type": "uncommittedChanges"}))
    await asyncio.sleep(0)
    await transport.incoming.put({"id": 7, "result": {"turn": {"id": "turn_review"}}})
    await review_task
    assert transport.sent[6] == {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "review/start",
            "params": {"threadId": "thr_2", "target": {"type": "uncommittedChanges"}},
    }

    await client.stop()

