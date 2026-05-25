from __future__ import annotations

from io import BytesIO
import json

import httpx
import pytest

from codex_gateway.gateways.telegram.bot_api import TelegramAPIError, TelegramBotAPI, chunk_text


@pytest.mark.asyncio
async def test_get_updates_uses_bot_api_endpoint() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/bottest-token/getUpdates"
        assert request.extensions["timeout"]["read"] == 40
        payload = json.loads(request.content)
        assert payload == {"offset": 12, "timeout": 30}
        return httpx.Response(200, json={"ok": True, "result": [{"update_id": 12}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.telegram.org")
    api = TelegramBotAPI("test-token", client=client)

    assert await api.get_updates(offset=12, timeout=30) == [{"update_id": 12}]

    await client.aclose()


@pytest.mark.asyncio
async def test_httpx_timeout_error_names_exception_when_message_is_empty() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.telegram.org")
    api = TelegramBotAPI("test-token", client=client)

    with pytest.raises(TelegramAPIError) as exc_info:
        await api.get_updates(timeout=30)

    assert "ReadTimeout" in str(exc_info.value)
    await client.aclose()


@pytest.mark.asyncio
async def test_send_message_chunks_long_text() -> None:
    requests: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(requests)}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.telegram.org")
    api = TelegramBotAPI("test-token", client=client)

    result = await api.send_message(42, "x" * 3601)

    assert [item["message_id"] for item in result] == [1, 2]
    assert len(requests[0]["text"]) <= 3500
    assert len(requests[1]["text"]) == 101

    await client.aclose()


@pytest.mark.asyncio
async def test_send_message_options_use_valid_bot_api_payload() -> None:
    requests: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/bottest-token/sendMessage"
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.telegram.org")
    api = TelegramBotAPI("test-token", client=client)

    await api.send_message(
        42,
        "<b>done</b>",
        parse_mode="HTML",
        reply_to_message_id=10,
        reply_markup={"inline_keyboard": [[{"text": "OK", "callback_data": "approval:t:accept"}]]},
    )

    assert requests == [
        {
            "chat_id": 42,
            "text": "<b>done</b>",
            "parse_mode": "HTML",
            "reply_to_message_id": 10,
            "reply_markup": {"inline_keyboard": [[{"text": "OK", "callback_data": "approval:t:accept"}]]},
        }
    ]
    await client.aclose()


@pytest.mark.asyncio
async def test_edit_message_text_and_answer_callback_query() -> None:
    seen_paths: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        return httpx.Response(200, json={"ok": True, "result": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.telegram.org")
    api = TelegramBotAPI("test-token", client=client)

    assert await api.edit_message_text(42, 9, "done") is True
    assert await api.answer_callback_query("cb", text="ok") is True

    assert seen_paths == [
        "/bottest-token/editMessageText",
        "/bottest-token/answerCallbackQuery",
    ]
    await client.aclose()


@pytest.mark.asyncio
async def test_edit_and_callback_options_use_valid_bot_api_payloads() -> None:
    requests: list[tuple[str, dict[str, object]]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"ok": True, "result": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.telegram.org")
    api = TelegramBotAPI("test-token", client=client)

    await api.edit_message_text(
        42,
        9,
        "<b>edited</b>",
        parse_mode="HTML",
        reply_markup={"inline_keyboard": [[]]},
    )
    await api.answer_callback_query("cb", text="ok", show_alert=True)

    assert requests == [
        (
            "/bottest-token/editMessageText",
            {
                "chat_id": 42,
                "message_id": 9,
                "text": "<b>edited</b>",
                "parse_mode": "HTML",
                "reply_markup": {"inline_keyboard": [[]]},
            },
        ),
        (
            "/bottest-token/answerCallbackQuery",
            {"callback_query_id": "cb", "text": "ok", "show_alert": True},
        ),
    ]
    await client.aclose()


@pytest.mark.asyncio
async def test_send_chat_action_uses_bot_api_endpoint() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/bottest-token/sendChatAction"
        assert json.loads(request.content) == {"chat_id": 42, "action": "typing"}
        return httpx.Response(200, json={"ok": True, "result": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.telegram.org")
    api = TelegramBotAPI("test-token", client=client)

    assert await api.send_chat_action(42, "typing") is True

    await client.aclose()


@pytest.mark.asyncio
async def test_set_message_reaction_uses_valid_bot_api_payload() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/bottest-token/setMessageReaction"
        assert json.loads(request.content) == {
            "chat_id": 42,
            "message_id": 10,
            "reaction": [{"type": "emoji", "emoji": "👍"}],
        }
        return httpx.Response(200, json={"ok": True, "result": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.telegram.org")
    api = TelegramBotAPI("test-token", client=client)

    assert await api.set_message_reaction(42, 10, "👍") is True

    await client.aclose()


@pytest.mark.asyncio
async def test_get_file_and_download_file_use_correct_urls() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getFile"):
            return httpx.Response(
                200,
                json={"ok": True, "result": {"file_id": "f1", "file_path": "docs/a.txt"}},
            )
        assert request.url.path == "/file/bottest-token/docs/a.txt"
        return httpx.Response(200, content=b"contents")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.telegram.org")
    api = TelegramBotAPI("test-token", client=client)

    file_info = await api.get_file("f1")
    assert file_info["file_path"] == "docs/a.txt"
    assert await api.download_file("docs/a.txt") == b"contents"

    await client.aclose()


@pytest.mark.asyncio
async def test_send_document_uses_guessed_fallback_and_explicit_content_types() -> None:
    bodies: list[bytes] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/bottest-token/sendDocument"
        bodies.append(request.content)
        return httpx.Response(200, json={"ok": True, "result": {"document": {"file_id": f"doc-{len(bodies)}"}}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.telegram.org")
    api = TelegramBotAPI("test-token", client=client)

    assert (await api.send_document(42, b"hello", filename="note.txt"))["document"]["file_id"] == "doc-1"
    assert (await api.send_document(42, BytesIO(b"raw"), filename="payload.unknownext"))["document"]["file_id"] == "doc-2"
    assert (
        await api.send_document(
            42,
            b"# title",
            filename="README.md",
            content_type="text/markdown",
        )
    )["document"]["file_id"] == "doc-3"

    assert b'filename="note.txt"' in bodies[0]
    assert b"Content-Type: text/plain" in bodies[0]
    assert b'filename="payload.unknownext"' in bodies[1]
    assert b"Content-Type: application/octet-stream" in bodies[1]
    assert b'filename="README.md"' in bodies[2]
    assert b"Content-Type: text/markdown" in bodies[2]

    await client.aclose()


@pytest.mark.asyncio
async def test_send_photo_and_video_use_native_multipart_payloads() -> None:
    requests: list[tuple[str, bytes]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.url.path, request.content))
        method = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(requests), "method": method}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.telegram.org")
    api = TelegramBotAPI("test-token", client=client)

    assert (await api.send_photo(42, b"jpeg bytes", filename="image.jpg", caption="Photo"))["method"] == "sendPhoto"
    assert (
        await api.send_video(
            42,
            BytesIO(b"mp4 bytes"),
            filename="clip.mp4",
            caption="Clip",
            content_type="video/mp4",
            duration=5,
            width=640,
            height=360,
        )
    )["method"] == "sendVideo"

    assert requests[0][0] == "/bottest-token/sendPhoto"
    assert b'name="photo"; filename="image.jpg"' in requests[0][1]
    assert b"Content-Type: image/jpeg" in requests[0][1]
    assert b'name="caption"' in requests[0][1]
    assert b"Photo" in requests[0][1]
    assert requests[1][0] == "/bottest-token/sendVideo"
    assert b'name="video"; filename="clip.mp4"' in requests[1][1]
    assert b"Content-Type: video/mp4" in requests[1][1]
    assert b'name="duration"' in requests[1][1]
    assert b"name=\"width\"" in requests[1][1]
    assert b"name=\"height\"" in requests[1][1]
    assert b"\r\n\r\n5\r\n" in requests[1][1]
    assert b"\r\n\r\n640\r\n" in requests[1][1]
    assert b"\r\n\r\n360\r\n" in requests[1][1]

    await client.aclose()


@pytest.mark.asyncio
async def test_token_is_redacted_from_errors() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"ok": False, "description": "bad token test-token"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.telegram.org")
    api = TelegramBotAPI("test-token", client=client)

    with pytest.raises(TelegramAPIError) as exc_info:
        await api.get_updates()

    assert "test-token" not in str(exc_info.value)
    assert "<redacted>" in str(exc_info.value)
    await client.aclose()


@pytest.mark.asyncio
async def test_httpx_exception_messages_are_redacted() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            "failed https://api.telegram.org/bottest-token/getUpdates",
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.telegram.org")
    api = TelegramBotAPI("test-token", client=client)

    with pytest.raises(TelegramAPIError) as exc_info:
        await api.get_updates()

    assert "test-token" not in str(exc_info.value)
    assert "<redacted>" in str(exc_info.value)
    await client.aclose()


def test_chunk_text_uses_conservative_limit() -> None:
    assert chunk_text("abc", limit=2) == ["ab", "c"]

