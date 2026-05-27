from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import shutil
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


LOGGER = logging.getLogger(__name__)
DEFAULT_APP_SERVER_RPC_TIMEOUT_SECONDS = 30.0
DEFAULT_APP_SERVER_HANDLER_TIMEOUT_SECONDS = 150.0
DEFAULT_APP_SERVER_NOTIFICATION_QUEUE_SIZE = 512
JSON_RPC_TIMEOUT_CODE = -32002


class JsonRpcError(RuntimeError):
    def __init__(self, message: str, *, code: int | None = None, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


@dataclass(frozen=True)
class AppServerEvent:
    method: str
    params: dict[str, Any]
    request_id: int | str | None = None


NotificationHandler = Callable[[AppServerEvent], None | Awaitable[None]]
_UNSET = object()


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
            raise JsonRpcError("App-server process is not running")
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


class AppServerClient:
    def __init__(
        self,
        *,
        transport: Any | None = None,
        command: str | tuple[str, ...] | None = None,
        on_notification: NotificationHandler | None = None,
        on_request: NotificationHandler | None = None,
        retry_delay_seconds: float = 0.25,
        request_timeout_seconds: float | None = DEFAULT_APP_SERVER_RPC_TIMEOUT_SECONDS,
        send_timeout_seconds: float | None = DEFAULT_APP_SERVER_RPC_TIMEOUT_SECONDS,
        server_request_timeout_seconds: float | None = DEFAULT_APP_SERVER_HANDLER_TIMEOUT_SECONDS,
        notification_queue_size: int = DEFAULT_APP_SERVER_NOTIFICATION_QUEUE_SIZE,
    ) -> None:
        self.transport = transport
        self.command = _normalize_command(command) if command is not None else None
        self.on_notification = on_notification
        self.on_request = on_request
        self.retry_delay_seconds = retry_delay_seconds
        self.request_timeout_seconds = request_timeout_seconds
        self.send_timeout_seconds = send_timeout_seconds
        self.server_request_timeout_seconds = server_request_timeout_seconds
        self.notification_queue_size = notification_queue_size
        self._next_id = 1
        self._pending: dict[int | str, asyncio.Future[Any]] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._notification_queue: asyncio.Queue[AppServerEvent] | None = None
        self._notification_task: asyncio.Task[None] | None = None
        self._handler_tasks: set[asyncio.Task[Any]] = set()

    async def start(self) -> None:
        if self.transport is None:
            if self.command is None:
                raise JsonRpcError("App-server command is required when no transport is provided")
            self.transport = StdioJsonRpcTransport(self.command)
            await self.transport.start()
        await self.start_reader()
        await self.initialize()

    async def start_reader(self) -> None:
        if self.transport is None:
            raise JsonRpcError("Transport is required")
        if self._reader_task is None or self._reader_task.done():
            self._reader_task = asyncio.create_task(self._read_loop())
        if self._notification_task is None or self._notification_task.done():
            queue_size = max(1, int(self.notification_queue_size))
            self._notification_queue = asyncio.Queue(maxsize=queue_size)
            self._notification_task = asyncio.create_task(self._notification_dispatch_loop())

    async def stop(self) -> None:
        self._fail_pending(JsonRpcError("App-server client stopped"))
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        if self._notification_task is not None:
            self._notification_task.cancel()
            try:
                await self._notification_task
            except asyncio.CancelledError:
                pass
        for task in list(self._handler_tasks):
            task.cancel()
        if self._handler_tasks:
            await asyncio.gather(*self._handler_tasks, return_exceptions=True)
        if self.transport is not None:
            await self.transport.close()

    async def wait_reader_stopped(self) -> None:
        task = self._reader_task
        if task is not None:
            await task

    async def initialize(self) -> Any:
        result = await self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "codex_gateway",
                    "title": "Codex Gateway",
                    "version": "0.1.0",
                },
                "capabilities": {"experimentalApi": True},
            },
        )
        await self.notify("initialized", {})
        return result

    async def thread_start(
        self,
        *,
        cwd: str,
        model: str | None = None,
        approval_policy: str | None = None,
        sandbox: str | None = None,
        permissions: str | None = None,
        dynamic_tools: list[dict[str, Any]] | None = None,
        developer_instructions: str | None = None,
    ) -> Any:
        params: dict[str, Any] = {"cwd": cwd}
        if model:
            params["model"] = model
        if approval_policy:
            params["approvalPolicy"] = approval_policy
        if sandbox:
            params["sandbox"] = sandbox
        if permissions:
            params["permissions"] = permissions
        if dynamic_tools is not None:
            params["dynamicTools"] = dynamic_tools
        if developer_instructions is not None:
            params["developerInstructions"] = developer_instructions
        return await self.request("thread/start", params)

    async def turn_start(
        self,
        *,
        thread_id: str,
        input_items: list[dict[str, Any]],
        cwd: str | None = None,
        approval_policy: str | None = None,
        sandbox_policy: dict[str, Any] | None = None,
        permissions: str | None = None,
    ) -> Any:
        params: dict[str, Any] = {"threadId": thread_id, "input": input_items}
        if cwd:
            params["cwd"] = cwd
        if approval_policy:
            params["approvalPolicy"] = approval_policy
        if sandbox_policy:
            params["sandboxPolicy"] = sandbox_policy
        if permissions:
            params["permissions"] = permissions
        return await self.request("turn/start", params)

    async def thread_resume(
        self,
        *,
        thread_id: str,
        cwd: str | None = None,
        approval_policy: str | None = None,
        sandbox: str | None = None,
        model: str | None = None,
        permissions: str | None = None,
        developer_instructions: str | None = None,
    ) -> Any:
        params: dict[str, Any] = {"threadId": thread_id}
        if cwd:
            params["cwd"] = cwd
        if approval_policy:
            params["approvalPolicy"] = approval_policy
        if sandbox:
            params["sandbox"] = sandbox
        if model:
            params["model"] = model
        if permissions:
            params["permissions"] = permissions
        if developer_instructions is not None:
            params["developerInstructions"] = developer_instructions
        return await self.request("thread/resume", params)

    async def thread_fork(
        self,
        *,
        thread_id: str,
        exclude_turns: bool = True,
        ephemeral: bool | None = None,
        cwd: str | None = None,
        model: str | None = None,
        approval_policy: str | None = None,
        sandbox: str | None = None,
        permissions: str | None = None,
        developer_instructions: str | None = None,
    ) -> Any:
        params: dict[str, Any] = {"threadId": thread_id, "excludeTurns": exclude_turns}
        if ephemeral is not None:
            params["ephemeral"] = ephemeral
        if cwd is not None:
            params["cwd"] = cwd
        if model is not None:
            params["model"] = model
        if approval_policy is not None:
            params["approvalPolicy"] = approval_policy
        if sandbox is not None:
            params["sandbox"] = sandbox
        if permissions is not None:
            params["permissions"] = permissions
        if developer_instructions is not None:
            params["developerInstructions"] = developer_instructions
        return await self.request("thread/fork", params)

    async def thread_compact_start(self, *, thread_id: str) -> Any:
        return await self.request("thread/compact/start", {"threadId": thread_id})

    async def thread_background_terminals_clean(self, *, thread_id: str) -> Any:
        return await self.request("thread/backgroundTerminals/clean", {"threadId": thread_id})

    async def thread_archive(self, *, thread_id: str) -> Any:
        return await self.request("thread/archive", {"threadId": thread_id})

    async def thread_unarchive(self, *, thread_id: str) -> Any:
        return await self.request("thread/unarchive", {"threadId": thread_id})

    async def thread_rollback(self, *, thread_id: str, num_turns: int = 1) -> Any:
        return await self.request("thread/rollback", {"threadId": thread_id, "numTurns": num_turns})

    async def thread_loaded_list(
        self,
        *,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Any:
        params: dict[str, Any] = {}
        if limit is not None:
            params["limit"] = limit
        if cursor is not None:
            params["cursor"] = cursor
        return await self.request("thread/loaded/list", params)

    async def thread_approve_guardian_denied_action(self, *, thread_id: str, event: Any) -> Any:
        return await self.request(
            "thread/approveGuardianDeniedAction",
            {"threadId": thread_id, "event": event},
        )

    async def thread_settings_update(
        self,
        *,
        thread_id: str,
        model: Any = _UNSET,
        approval_policy: Any = _UNSET,
        collaboration_mode: Any = _UNSET,
        effort: Any = _UNSET,
        permissions: Any = _UNSET,
        personality: Any = _UNSET,
    ) -> Any:
        params: dict[str, Any] = {"threadId": thread_id}
        _set_if_present(params, "model", model)
        _set_if_present(params, "approvalPolicy", approval_policy)
        _set_if_present(params, "collaborationMode", collaboration_mode)
        _set_if_present(params, "effort", effort)
        _set_if_present(params, "permissions", permissions)
        _set_if_present(params, "personality", personality)
        return await self.request("thread/settings/update", params)

    async def thread_set_name(self, *, thread_id: str, name: str) -> Any:
        return await self.request("thread/name/set", {"threadId": thread_id, "name": name})

    async def thread_goal_set(
        self,
        *,
        thread_id: str,
        objective: str | None = None,
        status: str | None = None,
        token_budget: int | None = None,
    ) -> Any:
        params: dict[str, Any] = {"threadId": thread_id}
        if objective is not None:
            params["objective"] = objective
        if status is not None:
            params["status"] = status
        if token_budget is not None:
            params["tokenBudget"] = token_budget
        return await self.request("thread/goal/set", params)

    async def thread_goal_get(self, *, thread_id: str) -> Any:
        return await self.request("thread/goal/get", {"threadId": thread_id})

    async def thread_goal_clear(self, *, thread_id: str) -> Any:
        return await self.request("thread/goal/clear", {"threadId": thread_id})

    async def thread_list(
        self,
        *,
        cwd: str | list[str] | None = None,
        search_term: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        archived: bool | None = None,
    ) -> Any:
        params: dict[str, Any] = {}
        if cwd is not None:
            params["cwd"] = cwd
        if search_term:
            params["searchTerm"] = search_term
        if limit is not None:
            params["limit"] = limit
        if cursor is not None:
            params["cursor"] = cursor
        if archived is not None:
            params["archived"] = archived
        return await self.request("thread/list", params)

    async def thread_read(self, *, thread_id: str, include_turns: bool = False) -> Any:
        return await self.request("thread/read", {"threadId": thread_id, "includeTurns": include_turns})

    async def thread_turns_items_list(
        self,
        *,
        thread_id: str,
        turn_id: str,
        sort_direction: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Any:
        params: dict[str, Any] = {"threadId": thread_id, "turnId": turn_id}
        if sort_direction is not None:
            params["sortDirection"] = sort_direction
        if limit is not None:
            params["limit"] = limit
        if cursor is not None:
            params["cursor"] = cursor
        return await self.request("thread/turns/items/list", params)

    async def thread_memory_mode_set(self, *, thread_id: str, mode: str) -> Any:
        return await self.request("thread/memoryMode/set", {"threadId": thread_id, "mode": mode})

    async def turn_interrupt(self, *, thread_id: str, turn_id: str) -> Any:
        return await self.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id})

    async def turn_steer(
        self,
        *,
        thread_id: str,
        expected_turn_id: str,
        input_items: list[dict[str, Any]],
        responsesapi_client_metadata: dict[str, str] | None = None,
    ) -> Any:
        params: dict[str, Any] = {
            "threadId": thread_id,
            "expectedTurnId": expected_turn_id,
            "input": input_items,
        }
        if responsesapi_client_metadata is not None:
            params["responsesapiClientMetadata"] = responsesapi_client_metadata
        return await self.request("turn/steer", params)

    async def review_start(self, *, thread_id: str, target: dict[str, Any] | None = None) -> Any:
        return await self.request(
            "review/start",
            {"threadId": thread_id, "target": target or {"type": "uncommittedChanges"}},
        )

    async def model_list(
        self,
        *,
        include_hidden: bool | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Any:
        params: dict[str, Any] = {}
        if include_hidden is not None:
            params["includeHidden"] = include_hidden
        if limit is not None:
            params["limit"] = limit
        if cursor is not None:
            params["cursor"] = cursor
        return await self.request("model/list", params)

    async def permission_profile_list(
        self,
        *,
        cwd: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Any:
        params: dict[str, Any] = {}
        if cwd is not None:
            params["cwd"] = cwd
        if limit is not None:
            params["limit"] = limit
        if cursor is not None:
            params["cursor"] = cursor
        return await self.request("permissionProfile/list", params)

    async def experimental_feature_list(
        self,
        *,
        thread_id: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Any:
        params: dict[str, Any] = {}
        if thread_id is not None:
            params["threadId"] = thread_id
        if limit is not None:
            params["limit"] = limit
        if cursor is not None:
            params["cursor"] = cursor
        return await self.request("experimentalFeature/list", params)

    async def experimental_feature_enablement_set(self, *, enablement: dict[str, bool]) -> Any:
        return await self.request("experimentalFeature/enablement/set", {"enablement": enablement})

    async def collaboration_mode_list(self) -> Any:
        return await self.request("collaborationMode/list", {})

    async def skills_list(
        self,
        *,
        cwds: list[str],
        force_reload: bool = False,
    ) -> Any:
        return await self.request(
            "skills/list",
            {"cwds": cwds, "forceReload": force_reload},
        )

    async def skills_config_write(
        self,
        *,
        enabled: bool,
        name: str | None = None,
        path: str | None = None,
    ) -> Any:
        params: dict[str, Any] = {"enabled": enabled}
        if name is not None:
            params["name"] = name
        if path is not None:
            params["path"] = path
        return await self.request("skills/config/write", params)

    async def app_list(
        self,
        *,
        thread_id: str | None = None,
        force_refetch: bool | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Any:
        params: dict[str, Any] = {}
        if thread_id is not None:
            params["threadId"] = thread_id
        if force_refetch is not None:
            params["forceRefetch"] = force_refetch
        if limit is not None:
            params["limit"] = limit
        if cursor is not None:
            params["cursor"] = cursor
        return await self.request("app/list", params)

    async def plugin_list(
        self,
        *,
        cwds: list[str] | None = None,
        marketplace_kinds: list[str] | None = None,
    ) -> Any:
        params: dict[str, Any] = {}
        if cwds is not None:
            params["cwds"] = cwds
        if marketplace_kinds is not None:
            params["marketplaceKinds"] = marketplace_kinds
        return await self.request("plugin/list", params)

    async def hooks_list(self, *, cwds: list[str]) -> Any:
        return await self.request("hooks/list", {"cwds": cwds})

    async def account_read(self, *, refresh_token: bool = False) -> Any:
        return await self.request("account/read", {"refreshToken": refresh_token})

    async def account_rate_limits_read(self) -> Any:
        return await self.request("account/rateLimits/read", {})

    async def mcp_server_status_list(
        self,
        *,
        detail: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Any:
        params: dict[str, Any] = {}
        if detail is not None:
            params["detail"] = detail
        if limit is not None:
            params["limit"] = limit
        if cursor is not None:
            params["cursor"] = cursor
        return await self.request("mcpServerStatus/list", params)

    async def config_mcp_server_reload(self) -> Any:
        return await self.request("config/mcpServer/reload", {})

    async def config_read(self, *, cwd: str | None = None, include_layers: bool = False) -> Any:
        params: dict[str, Any] = {"includeLayers": include_layers}
        if cwd is not None:
            params["cwd"] = cwd
        return await self.request("config/read", params)

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> Any:
        attempts = 0
        while True:
            try:
                return await self._request_with_timeout(method, params, timeout_seconds=timeout_seconds)
            except JsonRpcError as exc:
                if exc.code == -32001 and attempts == 0:
                    attempts += 1
                    if self.retry_delay_seconds > 0:
                        await asyncio.sleep(self.retry_delay_seconds)
                    continue
                raise

    async def _request_with_timeout(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> Any:
        timeout = self.request_timeout_seconds if timeout_seconds is None else timeout_seconds
        if timeout is None or timeout <= 0:
            return await self._request_once(method, params)
        try:
            async with asyncio.timeout(timeout):
                return await self._request_once(method, params)
        except TimeoutError as exc:
            raise JsonRpcError(
                f"App-server request timed out after {timeout:g}s: {method}",
                code=JSON_RPC_TIMEOUT_CODE,
                data={"method": method, "timeoutSeconds": timeout},
            ) from exc

    async def _request_once(self, method: str, params: dict[str, Any] | None = None) -> Any:
        if self.transport is None:
            raise JsonRpcError("Transport is required")
        request_id = self._next_id
        self._next_id += 1
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._pending[request_id] = future
        try:
            await self._transport_send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params or {},
                }
            )
            return await future
        except asyncio.CancelledError:
            self._pending.pop(request_id, None)
            future.cancel()
            raise
        except Exception:
            self._pending.pop(request_id, None)
            future.cancel()
            raise

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        if self.transport is None:
            raise JsonRpcError("Transport is required")
        await self._transport_send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    async def send_approval_decision(self, request_id: int | str, decision: str) -> None:
        await self._send_response(request_id, {"decision": decision})

    async def send_mcp_elicitation_response(self, request_id: int | str, action: str) -> None:
        await self._send_response(request_id, {"action": action})

    async def send_permissions_approval_response(
        self,
        request_id: int | str,
        permissions: dict[str, Any],
    ) -> None:
        await self._send_response(request_id, {"permissions": permissions, "scope": "turn"})

    async def send_tool_user_input_response(
        self,
        request_id: int | str,
        answers: dict[str, list[str]],
    ) -> None:
        await self._send_response(
            request_id,
            {
                "answers": {
                    question_id: {"answers": [str(answer) for answer in values]}
                    for question_id, values in answers.items()
                }
            },
        )

    async def send_dynamic_tool_result(
        self,
        request_id: int | str,
        content: list[dict[str, Any]],
    ) -> None:
        await self._send_response(
            request_id,
            {"contentItems": [_tool_result_item(item) for item in content], "success": True},
        )

    async def send_error_response(
        self,
        request_id: int | str,
        message: str,
        *,
        code: int = -32000,
    ) -> None:
        if self.transport is None:
            raise JsonRpcError("Transport is required")
        await self._transport_send(
            {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
        )

    async def _send_response(self, request_id: int | str, result: dict[str, Any]) -> None:
        if self.transport is None:
            raise JsonRpcError("Transport is required")
        await self._transport_send({"jsonrpc": "2.0", "id": request_id, "result": result})

    async def _transport_send(self, message: dict[str, Any]) -> None:
        if self.transport is None:
            raise JsonRpcError("Transport is required")
        try:
            if self.send_timeout_seconds is None or self.send_timeout_seconds <= 0:
                await self.transport.send(message)
            else:
                await asyncio.wait_for(self.transport.send(message), timeout=self.send_timeout_seconds)
        except JsonRpcError:
            raise
        except asyncio.TimeoutError as exc:
            raise JsonRpcError(
                f"App-server transport send timed out after {self.send_timeout_seconds:g}s"
            ) from exc
        except Exception as exc:
            detail = str(exc) or exc.__class__.__name__
            raise JsonRpcError(f"App-server transport send failed: {detail}") from exc

    async def _read_loop(self) -> None:
        try:
            while True:
                if self.transport is None:
                    self._fail_pending(JsonRpcError("App-server transport closed"))
                    return
                message = await self.transport.receive()
                if message is None:
                    self._fail_pending(JsonRpcError("App-server transport closed"))
                    return
                await self._handle_message(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._fail_pending(JsonRpcError(f"App-server reader failed: {exc}"))

    async def _handle_message(self, message: dict[str, Any]) -> None:
        if "id" in message and ("result" in message or "error" in message):
            request_id = message["id"]
            future = self._pending.pop(request_id, None)
            if future is None or future.done():
                return
            if "error" in message:
                error = message.get("error") or {}
                future.set_exception(
                    JsonRpcError(
                        str(error.get("message") or "JSON-RPC error"),
                        code=error.get("code"),
                        data=error.get("data"),
                    )
                )
            else:
                future.set_result(message.get("result"))
            return

        method = str(message.get("method") or "")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        event = AppServerEvent(method=method, params=params, request_id=message.get("id"))
        if "id" in message:
            self._track_handler_task(self._run_request_handler(event, message["id"]))
        else:
            self._queue_notification(event)

    def _queue_notification(self, event: AppServerEvent) -> None:
        queue = self._notification_queue
        if queue is None:
            self._track_handler_task(self._run_notification_handler(event))
            return
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            if _must_deliver_notification(event.method):
                LOGGER.error(
                    "App-server notification queue full; dispatching required event outside queue: %s",
                    event.method,
                )
                self._track_handler_task(self._run_notification_handler(event))
                return
            LOGGER.warning("Dropped low-priority app-server notification under pressure: %s", event.method)

    async def _notification_dispatch_loop(self) -> None:
        queue = self._notification_queue
        if queue is None:
            return
        while True:
            event = await queue.get()
            try:
                await self._run_notification_handler(event)
            finally:
                queue.task_done()

    async def _run_notification_handler(self, event: AppServerEvent) -> None:
        try:
            await _maybe_await(self.on_notification, event)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("App-server notification handler failed for %s", event.method)

    async def _run_request_handler(self, event: AppServerEvent, fallback_request_id: int | str) -> None:
        try:
            if self.server_request_timeout_seconds is None or self.server_request_timeout_seconds <= 0:
                await _maybe_await(self.on_request, event)
            else:
                async with asyncio.timeout(self.server_request_timeout_seconds):
                    await _maybe_await(self.on_request, event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOGGER.exception("App-server request handler failed for %s", event.method)
            try:
                request_id = event.request_id if event.request_id is not None else fallback_request_id
                await self.send_error_response(request_id, str(exc))
            except Exception:
                LOGGER.exception("Failed to send app-server error response for %s", event.method)

    def _track_handler_task(self, awaitable: Awaitable[Any]) -> None:
        task = asyncio.create_task(awaitable)
        self._handler_tasks.add(task)
        task.add_done_callback(self._finish_handler_task)

    def _finish_handler_task(self, task: asyncio.Task[Any]) -> None:
        self._handler_tasks.discard(task)
        if task.cancelled():
            return
        try:
            task.result()
        except Exception:
            LOGGER.exception("App-server handler task failed")

    def _fail_pending(self, exc: Exception) -> None:
        pending = list(self._pending.values())
        self._pending.clear()
        for future in pending:
            if not future.done():
                future.set_exception(exc)


async def _maybe_await(handler: NotificationHandler | None, event: AppServerEvent) -> None:
    if handler is None:
        return
    result = handler(event)
    if asyncio.iscoroutine(result):
        await result


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


def _set_if_present(params: dict[str, Any], key: str, value: Any) -> None:
    if value is not _UNSET:
        params[key] = value


def _tool_result_item(item: dict[str, Any]) -> dict[str, Any]:
    if item.get("type") == "text":
        return {"type": "inputText", "text": str(item.get("text") or "")}
    return item


def _must_deliver_notification(method: str) -> bool:
    return (
        method == "turn/completed"
        or method.endswith("/request")
        or method.endswith("/completed")
        or "requestApproval" in method
        or "requestUserInput" in method
        or "elicitation/request" in method
    )
