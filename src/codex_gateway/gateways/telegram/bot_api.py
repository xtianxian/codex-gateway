from __future__ import annotations

import json
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

    async def recreate_client(self) -> bool:
        if not self._owns_client:
            return False
        old_client = self.client
        self.client = httpx.AsyncClient(base_url=self.base_url)
        await old_client.aclose()
        return True

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

    async def send_animation(
        self,
        chat_id: str | int,
        animation: bytes | BytesIO,
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
            "sendAnimation",
            "animation",
            chat_id,
            animation,
            filename=filename,
            caption=caption,
            content_type=content_type,
            extra=extra,
        )

    async def send_audio(
        self,
        chat_id: str | int,
        audio: bytes | BytesIO,
        *,
        filename: str,
        caption: str | None = None,
        content_type: str | None = None,
        duration: int | None = None,
        performer: str | None = None,
        title: str | None = None,
    ) -> Any:
        extra: dict[str, Any] = {}
        if duration is not None:
            extra["duration"] = duration
        if performer:
            extra["performer"] = performer
        if title:
            extra["title"] = title
        return await self._send_multipart_media(
            "sendAudio",
            "audio",
            chat_id,
            audio,
            filename=filename,
            caption=caption,
            content_type=content_type,
            extra=extra,
        )

    async def send_voice(
        self,
        chat_id: str | int,
        voice: bytes | BytesIO,
        *,
        filename: str,
        caption: str | None = None,
        content_type: str | None = None,
        duration: int | None = None,
    ) -> Any:
        extra: dict[str, Any] = {}
        if duration is not None:
            extra["duration"] = duration
        return await self._send_multipart_media(
            "sendVoice",
            "voice",
            chat_id,
            voice,
            filename=filename,
            caption=caption,
            content_type=content_type,
            extra=extra,
        )

    async def send_video_note(
        self,
        chat_id: str | int,
        video_note: bytes | BytesIO,
        *,
        filename: str,
        content_type: str | None = None,
        duration: int | None = None,
        length: int | None = None,
    ) -> Any:
        extra: dict[str, Any] = {}
        if duration is not None:
            extra["duration"] = duration
        if length is not None:
            extra["length"] = length
        return await self._send_multipart_media(
            "sendVideoNote",
            "video_note",
            chat_id,
            video_note,
            filename=filename,
            content_type=content_type,
            extra=extra,
        )

    async def send_sticker(
        self,
        chat_id: str | int,
        sticker: bytes | BytesIO,
        *,
        filename: str,
        content_type: str | None = None,
        emoji: str | None = None,
    ) -> Any:
        extra: dict[str, Any] = {}
        if emoji:
            extra["emoji"] = emoji
        return await self._send_multipart_media(
            "sendSticker",
            "sticker",
            chat_id,
            sticker,
            filename=filename,
            content_type=content_type,
            extra=extra,
        )

    async def send_live_photo(
        self,
        chat_id: str | int,
        live_photo: bytes | BytesIO,
        photo: bytes | BytesIO,
        *,
        live_photo_filename: str,
        photo_filename: str,
        caption: str | None = None,
        live_photo_content_type: str | None = None,
        photo_content_type: str | None = None,
    ) -> Any:
        data: dict[str, Any] = {"chat_id": str(chat_id)}
        if caption:
            data["caption"] = caption
        return await self._send_multipart_files(
            "sendLivePhoto",
            data=data,
            files={
                "live_photo": (live_photo_filename, live_photo, live_photo_content_type),
                "photo": (photo_filename, photo, photo_content_type),
            },
        )

    async def send_media_group(
        self,
        chat_id: str | int,
        media: list[dict[str, Any]],
        *,
        files: dict[str, tuple[str, bytes | BytesIO, str | None]] | None = None,
    ) -> Any:
        payload: dict[str, Any] = {"chat_id": chat_id, "media": media}
        if files:
            return await self._send_multipart_files(
                "sendMediaGroup",
                data={"chat_id": str(chat_id), "media": media},
                files=files,
            )
        return await self._request("sendMediaGroup", payload)

    async def send_paid_media(
        self,
        chat_id: str | int,
        star_count: int,
        media: list[dict[str, Any]],
        *,
        caption: str | None = None,
        payload: str | None = None,
        files: dict[str, tuple[str, bytes | BytesIO, str | None]] | None = None,
    ) -> Any:
        request_payload: dict[str, Any] = {"chat_id": chat_id, "star_count": star_count, "media": media}
        if caption:
            request_payload["caption"] = caption
        if payload:
            request_payload["payload"] = payload
        if files:
            return await self._send_multipart_files(
                "sendPaidMedia",
                data=request_payload,
                files=files,
            )
        return await self._request("sendPaidMedia", request_payload)

    async def send_contact(
        self,
        chat_id: str | int,
        phone_number: str,
        first_name: str,
        *,
        last_name: str | None = None,
        vcard: str | None = None,
    ) -> Any:
        payload: dict[str, Any] = {"chat_id": chat_id, "phone_number": phone_number, "first_name": first_name}
        if last_name:
            payload["last_name"] = last_name
        if vcard:
            payload["vcard"] = vcard
        return await self._request("sendContact", payload)

    async def send_location(
        self,
        chat_id: str | int,
        latitude: float,
        longitude: float,
        **options: Any,
    ) -> Any:
        payload: dict[str, Any] = {"chat_id": chat_id, "latitude": latitude, "longitude": longitude}
        payload.update({key: value for key, value in options.items() if value is not None})
        return await self._request("sendLocation", payload)

    async def send_venue(
        self,
        chat_id: str | int,
        latitude: float,
        longitude: float,
        title: str,
        address: str,
        **options: Any,
    ) -> Any:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "latitude": latitude,
            "longitude": longitude,
            "title": title,
            "address": address,
        }
        payload.update({key: value for key, value in options.items() if value is not None})
        return await self._request("sendVenue", payload)

    async def send_poll(
        self,
        chat_id: str | int,
        question: str,
        options: list[str] | list[dict[str, Any]],
        **settings: Any,
    ) -> Any:
        normalized_options: list[str] | list[dict[str, Any]]
        if all(isinstance(option, str) for option in options):
            normalized_options = [{"text": str(option)} for option in options]
        else:
            normalized_options = options
        payload: dict[str, Any] = {"chat_id": chat_id, "question": question, "options": normalized_options}
        payload.update({key: value for key, value in settings.items() if value is not None})
        return await self._request("sendPoll", payload)

    async def send_checklist(
        self,
        chat_id: str | int,
        business_connection_id: str,
        checklist: dict[str, Any],
    ) -> Any:
        return await self._request(
            "sendChecklist",
            {
                "chat_id": chat_id,
                "business_connection_id": business_connection_id,
                "checklist": checklist,
            },
        )

    async def send_dice(self, chat_id: str | int, *, emoji: str | None = None) -> Any:
        payload: dict[str, Any] = {"chat_id": chat_id}
        if emoji:
            payload["emoji"] = emoji
        return await self._request("sendDice", payload)

    async def copy_message(
        self,
        chat_id: str | int,
        from_chat_id: str | int,
        message_id: int,
        **options: Any,
    ) -> Any:
        payload: dict[str, Any] = {"chat_id": chat_id, "from_chat_id": from_chat_id, "message_id": message_id}
        payload.update({key: value for key, value in options.items() if value is not None})
        return await self._request("copyMessage", payload)

    async def forward_message(
        self,
        chat_id: str | int,
        from_chat_id: str | int,
        message_id: int,
        **options: Any,
    ) -> Any:
        payload: dict[str, Any] = {"chat_id": chat_id, "from_chat_id": from_chat_id, "message_id": message_id}
        payload.update({key: value for key, value in options.items() if value is not None})
        return await self._request("forwardMessage", payload)

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
        data: dict[str, Any] = {"chat_id": str(chat_id)}
        if caption:
            data["caption"] = caption
        for key, value in (extra or {}).items():
            if value is not None:
                data[key] = _multipart_value(value)
        raw = media.getvalue() if isinstance(media, BytesIO) else media
        resolved_content_type = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        return await self._post_multipart(
            method,
            data=data,
            files={field_name: (filename, raw, resolved_content_type)},
        )

    async def _send_multipart_files(
        self,
        method: str,
        *,
        data: dict[str, Any],
        files: dict[str, tuple[str, bytes | BytesIO, str | None]],
    ) -> Any:
        file_payload: dict[str, tuple[str, bytes, str]] = {}
        for field_name, (filename, raw, content_type) in files.items():
            resolved_content_type = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
            file_payload[field_name] = (
                filename,
                raw.getvalue() if isinstance(raw, BytesIO) else raw,
                resolved_content_type,
            )
        return await self._post_multipart(method, data=data, files=file_payload)

    async def _post_multipart(
        self,
        method: str,
        *,
        data: dict[str, Any],
        files: dict[str, tuple[str, bytes, str]],
    ) -> Any:
        url = f"{self.base_url}/bot{self.token}/{method}"
        form_data = {key: _multipart_value(value) for key, value in data.items() if value is not None}
        try:
            response = await self.client.post(url, data=form_data, files=files)
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


def _multipart_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"))
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
