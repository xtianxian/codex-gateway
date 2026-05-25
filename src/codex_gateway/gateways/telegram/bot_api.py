from __future__ import annotations

import mimetypes
from io import BytesIO
from typing import Any

import httpx


MAX_TELEGRAM_TEXT = 3500


class TelegramAPIError(RuntimeError):
    pass


class TelegramBotAPI:
    def __init__(
        self,
        token: str,
        *,
        client: httpx.AsyncClient | None = None,
        base_url: str = "https://api.telegram.org",
    ) -> None:
        self.token = token
        self.base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(base_url=self.base_url)

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def get_updates(
        self,
        *,
        offset: int | None = None,
        timeout: int | None = None,
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {}
        if offset is not None:
            payload["offset"] = offset
        if timeout is not None:
            payload["timeout"] = timeout
        request_timeout = (timeout + 10) if timeout is not None else None
        return await self._request("getUpdates", payload, timeout=request_timeout)

    async def send_message(
        self,
        chat_id: str | int,
        text: str,
        *,
        parse_mode: str | None = None,
        reply_to_message_id: int | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        results = []
        for chunk in chunk_text(text):
            payload: dict[str, Any] = {"chat_id": chat_id, "text": chunk}
            if parse_mode:
                payload["parse_mode"] = parse_mode
            if reply_to_message_id is not None:
                payload["reply_to_message_id"] = reply_to_message_id
            if reply_markup:
                payload["reply_markup"] = reply_markup
            results.append(await self._request("sendMessage", payload))
        return results

    async def edit_message_text(
        self,
        chat_id: str | int,
        message_id: int,
        text: str,
        *,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> Any:
        payload: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return await self._request("editMessageText", payload)

    async def answer_callback_query(
        self,
        callback_query_id: str,
        *,
        text: str | None = None,
        show_alert: bool = False,
    ) -> Any:
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        if show_alert:
            payload["show_alert"] = True
        return await self._request("answerCallbackQuery", payload)

    async def send_chat_action(self, chat_id: str | int, action: str = "typing") -> Any:
        return await self._request("sendChatAction", {"chat_id": chat_id, "action": action})

    async def send_document(
        self,
        chat_id: str | int,
        document: bytes | BytesIO,
        *,
        filename: str,
        caption: str | None = None,
        content_type: str | None = None,
    ) -> Any:
        return await self._send_multipart_media(
            "sendDocument",
            "document",
            chat_id,
            document,
            filename=filename,
            caption=caption,
            content_type=content_type,
        )

    async def send_photo(
        self,
        chat_id: str | int,
        photo: bytes | BytesIO,
        *,
        filename: str,
        caption: str | None = None,
        content_type: str | None = None,
    ) -> Any:
        return await self._send_multipart_media(
            "sendPhoto",
            "photo",
            chat_id,
            photo,
            filename=filename,
            caption=caption,
            content_type=content_type,
        )

    async def send_video(
        self,
        chat_id: str | int,
        video: bytes | BytesIO,
        *,
        filename: str,
        caption: str | None = None,
        content_type: str | None = None,
        duration: int | None = None,
        width: int | None = None,
        height: int | None = None,
    ) -> Any:
        extra: dict[str, Any] = {}
        if duration is not None:
            extra["duration"] = duration
        if width is not None:
            extra["width"] = width
        if height is not None:
            extra["height"] = height
        return await self._send_multipart_media(
            "sendVideo",
            "video",
            chat_id,
            video,
            filename=filename,
            caption=caption,
            content_type=content_type,
            extra=extra,
        )

    async def set_my_commands(self, commands: list[dict[str, str]]) -> Any:
        return await self._request("setMyCommands", {"commands": commands})

    async def get_file(self, file_id: str) -> dict[str, Any]:
        return await self._request("getFile", {"file_id": file_id})

    async def download_file(self, file_path: str) -> bytes:
        url = f"{self.base_url}/file/bot{self.token}/{file_path.lstrip('/')}"
        try:
            response = await self.client.get(url)
        except httpx.HTTPError as exc:
            raise TelegramAPIError(self._redact(f"Telegram file download failed: {exc}")) from exc
        if response.status_code >= 400:
            raise TelegramAPIError(self._redact(f"Telegram file download failed: {response.status_code} {response.text}"))
        return response.content

    async def set_message_reaction(
        self,
        chat_id: str | int,
        message_id: int,
        emoji: str,
    ) -> Any:
        return await self._request(
            "setMessageReaction",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "reaction": [{"type": "emoji", "emoji": emoji}],
            },
        )

    async def _request(
        self,
        method: str,
        payload: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> Any:
        url = f"{self.base_url}/bot{self.token}/{method}"
        try:
            response = await self.client.post(url, json=payload, timeout=timeout)
        except httpx.HTTPError as exc:
            detail = str(exc) or exc.__class__.__name__
            raise TelegramAPIError(
                self._redact(f"Telegram API {method} failed: {detail}")
            ) from exc
        body_text = response.text
        try:
            data = response.json()
        except ValueError:
            data = None
        if response.status_code >= 400 or not isinstance(data, dict) or not data.get("ok", False):
            description = ""
            if isinstance(data, dict):
                description = str(data.get("description") or "")
            raise TelegramAPIError(
                self._redact(
                    f"Telegram API {method} failed: {response.status_code} {description or body_text}"
                )
            )
        return data.get("result")

    async def _send_multipart_media(
        self,
        method: str,
        field_name: str,
        chat_id: str | int,
        media: bytes | BytesIO,
        *,
        filename: str,
        caption: str | None = None,
        content_type: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.base_url}/bot{self.token}/{method}"
        data: dict[str, Any] = {"chat_id": str(chat_id)}
        if caption:
            data["caption"] = caption
        for key, value in (extra or {}).items():
            if value is not None:
                data[key] = str(value)
        raw = media.getvalue() if isinstance(media, BytesIO) else media
        resolved_content_type = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        try:
            response = await self.client.post(
                url,
                data=data,
                files={field_name: (filename, raw, resolved_content_type)},
            )
        except httpx.HTTPError as exc:
            detail = str(exc) or exc.__class__.__name__
            raise TelegramAPIError(self._redact(f"Telegram API {method} failed: {detail}")) from exc
        body_text = response.text
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if response.status_code >= 400 or not isinstance(payload, dict) or not payload.get("ok", False):
            description = ""
            if isinstance(payload, dict):
                description = str(payload.get("description") or "")
            raise TelegramAPIError(
                self._redact(f"Telegram API {method} failed: {response.status_code} {description or body_text}")
            )
        return payload.get("result")

    def _redact(self, value: str) -> str:
        return value.replace(self.token, "<redacted>")


def chunk_text(text: str, *, limit: int = MAX_TELEGRAM_TEXT) -> list[str]:
    if text == "":
        return [""]
    return [text[index : index + limit] for index in range(0, len(text), limit)]
