from __future__ import annotations

import hashlib
import json
from typing import Any


def telegram_dynamic_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "telegram_reply",
            "description": "Send a Telegram message to the active chat.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "parse_mode": {"type": "string"},
                    "reply_to_message_id": {"type": "integer"},
                },
                "required": ["text"],
            },
        },
        {
            "name": "telegram_react",
            "description": "React to a Telegram message in the active chat when supported.",
            "inputSchema": {
                "type": "object",
                "properties": {"emoji": {"type": "string"}, "message_id": {"type": "integer"}},
                "required": ["emoji"],
            },
        },
        {
            "name": "telegram_edit_message",
            "description": "Edit a bridge-owned Telegram message in the active chat.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "message_id": {"type": "integer"},
                    "text": {"type": "string"},
                    "parse_mode": {"type": "string"},
                },
                "required": ["message_id", "text"],
            },
        },
        {
            "name": "telegram_download_attachment",
            "description": "Return local metadata for a Telegram file attached to the current turn.",
            "inputSchema": {
                "type": "object",
                "properties": {"file_id": {"type": "string"}},
                "required": ["file_id"],
            },
        },
        {
            "name": "telegram_send_photo",
            "description": "Send a local image file from the active workspace as a native Telegram photo.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "caption": {"type": "string"},
                    "filename": {"type": "string"},
                    "content_type": {"type": "string"},
                },
                "required": ["path"],
            },
        },
        {
            "name": "telegram_send_video",
            "description": "Send a local video file from the active workspace as a native Telegram video.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "caption": {"type": "string"},
                    "filename": {"type": "string"},
                    "content_type": {"type": "string"},
                    "duration": {"type": "integer"},
                    "width": {"type": "integer"},
                    "height": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
        {
            "name": "telegram_send_document",
            "description": "Send a local file from the active workspace to the active Telegram chat.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "caption": {"type": "string"},
                    "filename": {"type": "string"},
                    "content_type": {"type": "string"},
                },
                "required": ["path"],
            },
        },
    ]


def _dynamic_tools_fingerprint() -> str:
    payload = json.dumps(telegram_dynamic_tools(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
