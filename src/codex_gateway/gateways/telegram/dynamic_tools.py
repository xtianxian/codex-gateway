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
        {
            "name": "telegram_send_animation",
            "description": "Send a local GIF or MPEG4 animation file from the active workspace as a native Telegram animation.",
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
            "name": "telegram_send_audio",
            "description": "Send a local MP3 or M4A file from the active workspace as native Telegram audio.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "caption": {"type": "string"},
                    "filename": {"type": "string"},
                    "content_type": {"type": "string"},
                    "duration": {"type": "integer"},
                    "performer": {"type": "string"},
                    "title": {"type": "string"},
                },
                "required": ["path"],
            },
        },
        {
            "name": "telegram_send_voice",
            "description": "Send a local audio file from the active workspace as a native Telegram voice message.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "caption": {"type": "string"},
                    "filename": {"type": "string"},
                    "content_type": {"type": "string"},
                    "duration": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
        {
            "name": "telegram_send_video_note",
            "description": "Send a local video file from the active workspace as a native Telegram video note.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "filename": {"type": "string"},
                    "content_type": {"type": "string"},
                    "duration": {"type": "integer"},
                    "length": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
        {
            "name": "telegram_send_sticker",
            "description": "Send a local WEBP, TGS, or WEBM file from the active workspace as a native Telegram sticker.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "filename": {"type": "string"},
                    "content_type": {"type": "string"},
                    "emoji": {"type": "string"},
                },
                "required": ["path"],
            },
        },
        {
            "name": "telegram_send_live_photo",
            "description": "Send a local live-photo video and static photo from the active workspace as a native Telegram live photo.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "live_photo_path": {"type": "string"},
                    "photo_path": {"type": "string"},
                    "caption": {"type": "string"},
                    "live_photo_filename": {"type": "string"},
                    "photo_filename": {"type": "string"},
                    "live_photo_content_type": {"type": "string"},
                    "photo_content_type": {"type": "string"},
                },
                "required": ["live_photo_path", "photo_path"],
            },
        },
        {
            "name": "telegram_send_media_group",
            "description": "Send a native Telegram media group from local active-workspace files.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "media": {
                        "type": "array",
                        "items": {"type": "object"},
                    },
                },
                "required": ["media"],
            },
        },
        {
            "name": "telegram_send_paid_media",
            "description": "Send native Telegram paid media from local active-workspace files when the bot account is eligible.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "star_count": {"type": "integer"},
                    "media": {
                        "type": "array",
                        "items": {"type": "object"},
                    },
                    "caption": {"type": "string"},
                    "payload": {"type": "string"},
                },
                "required": ["star_count", "media"],
            },
        },
        {
            "name": "telegram_send_contact",
            "description": "Send a native Telegram contact to the active chat.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "phone_number": {"type": "string"},
                    "first_name": {"type": "string"},
                    "last_name": {"type": "string"},
                    "vcard": {"type": "string"},
                },
                "required": ["phone_number", "first_name"],
            },
        },
        {
            "name": "telegram_send_location",
            "description": "Send a native Telegram location to the active chat.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "latitude": {"type": "number"},
                    "longitude": {"type": "number"},
                    "horizontal_accuracy": {"type": "number"},
                    "live_period": {"type": "integer"},
                    "heading": {"type": "integer"},
                    "proximity_alert_radius": {"type": "integer"},
                },
                "required": ["latitude", "longitude"],
            },
        },
        {
            "name": "telegram_send_venue",
            "description": "Send a native Telegram venue to the active chat.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "latitude": {"type": "number"},
                    "longitude": {"type": "number"},
                    "title": {"type": "string"},
                    "address": {"type": "string"},
                    "foursquare_id": {"type": "string"},
                    "foursquare_type": {"type": "string"},
                    "google_place_id": {"type": "string"},
                    "google_place_type": {"type": "string"},
                },
                "required": ["latitude", "longitude", "title", "address"],
            },
        },
        {
            "name": "telegram_send_poll",
            "description": "Send a native Telegram poll to the active chat.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "options": {"type": "array", "items": {}},
                    "is_anonymous": {"type": "boolean"},
                    "type": {"type": "string"},
                    "allows_multiple_answers": {"type": "boolean"},
                    "correct_option_id": {"type": "integer"},
                    "explanation": {"type": "string"},
                    "open_period": {"type": "integer"},
                    "close_date": {"type": "integer"},
                    "is_closed": {"type": "boolean"},
                },
                "required": ["question", "options"],
            },
        },
        {
            "name": "telegram_send_checklist",
            "description": "Send a native Telegram checklist when a business connection id is available.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "business_connection_id": {"type": "string"},
                    "title": {"type": "string"},
                    "tasks": {"type": "array", "items": {}},
                    "others_can_add_tasks": {"type": "boolean"},
                    "others_can_mark_tasks_as_done": {"type": "boolean"},
                },
                "required": ["business_connection_id", "title", "tasks"],
            },
        },
        {
            "name": "telegram_send_dice",
            "description": "Send a native Telegram dice animation to the active chat.",
            "inputSchema": {
                "type": "object",
                "properties": {"emoji": {"type": "string"}},
            },
        },
        {
            "name": "telegram_copy_current_message",
            "description": "Copy the current inbound Telegram message back to the active chat as a fallback for payloads that cannot be recreated.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "caption": {"type": "string"},
                    "parse_mode": {"type": "string"},
                },
            },
        },
        {
            "name": "telegram_forward_current_message",
            "description": "Forward the current inbound Telegram message back to the active chat as a fallback for payloads that cannot be recreated.",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]


def _dynamic_tools_fingerprint() -> str:
    payload = json.dumps(telegram_dynamic_tools(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
