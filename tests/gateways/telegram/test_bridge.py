from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from codex_gateway.backends.codex_app_server.client import AppServerEvent, JsonRpcError
from codex_gateway.backends.codex_app_server.protocol import generated_server_request_methods
from codex_gateway.core.commands import default_command_registry
from codex_gateway.gateways.telegram.access import AccessManager
from codex_gateway.gateways.telegram.bridge import (
    PLAN_CHOICE_TEXT,
    TELEGRAM_SERVER_REQUEST_SUPPORT,
    TELEGRAM_GATEWAY_DEVELOPER_INSTRUCTIONS,
    TelegramBridge,
    _dynamic_tools_fingerprint,
    handle_update_with_recovery,
    telegram_dynamic_tools,
)
from codex_gateway.gateways.telegram.commands import (
    APP_SERVER_COMMANDS,
    CODEX_TURN_COMMANDS,
    LOCAL_COMMANDS,
    THREAD_COMMANDS,
    UNSUPPORTED_COMMANDS,
)
from codex_gateway.gateways.telegram.bot_api import TelegramAPIError
from codex_gateway.gateways.telegram.config import TelegramSettings
from codex_gateway.gateways.telegram.state import TelegramStateStore


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.documents: list[dict[str, Any]] = []
        self.photos: list[dict[str, Any]] = []
        self.videos: list[dict[str, Any]] = []
        self.animations: list[dict[str, Any]] = []
        self.audios: list[dict[str, Any]] = []
        self.voices: list[dict[str, Any]] = []
        self.video_notes: list[dict[str, Any]] = []
        self.stickers: list[dict[str, Any]] = []
        self.live_photos: list[dict[str, Any]] = []
        self.media_groups: list[dict[str, Any]] = []
        self.paid_media: list[dict[str, Any]] = []
        self.contacts: list[dict[str, Any]] = []
        self.locations: list[dict[str, Any]] = []
        self.venues: list[dict[str, Any]] = []
        self.polls: list[dict[str, Any]] = []
        self.checklists: list[dict[str, Any]] = []
        self.dice: list[dict[str, Any]] = []
        self.copied_messages: list[dict[str, Any]] = []
        self.forwarded_messages: list[dict[str, Any]] = []
        self.edits: list[dict[str, Any]] = []
        self.answers: list[dict[str, Any]] = []
        self.reactions: list[dict[str, Any]] = []
        self.files: dict[str, dict[str, Any]] = {}
        self.downloads: dict[str, bytes] = {}
        self.supports_reactions = True
        self.next_message_id = 1000

    async def send_message(self, chat_id: str | int, text: str, **kwargs: Any) -> list[dict[str, Any]]:
        self.next_message_id += 1
        message = {
            "message_id": self.next_message_id,
            "chat": {"id": chat_id},
            "text": text,
            **kwargs,
        }
        self.messages.append(message)
        return [message]

    async def send_document(
        self,
        chat_id: str | int,
        document: bytes,
        *,
        filename: str,
        caption: str | None = None,
        content_type: str | None = None,
    ) -> dict[str, Any]:
        self.next_message_id += 1
        message = {
            "message_id": self.next_message_id,
            "chat": {"id": chat_id},
            "document": document,
            "filename": filename,
            "caption": caption,
            "content_type": content_type,
        }
        self.documents.append(message)
        return message

    async def send_photo(
        self,
        chat_id: str | int,
        photo: bytes,
        *,
        filename: str,
        caption: str | None = None,
        content_type: str | None = None,
    ) -> dict[str, Any]:
        self.next_message_id += 1
        message = {
            "message_id": self.next_message_id,
            "chat": {"id": chat_id},
            "photo": photo,
            "filename": filename,
            "caption": caption,
            "content_type": content_type,
        }
        self.photos.append(message)
        return message

    async def send_video(
        self,
        chat_id: str | int,
        video: bytes,
        *,
        filename: str,
        caption: str | None = None,
        content_type: str | None = None,
        duration: int | None = None,
        width: int | None = None,
        height: int | None = None,
    ) -> dict[str, Any]:
        self.next_message_id += 1
        message = {
            "message_id": self.next_message_id,
            "chat": {"id": chat_id},
            "video": video,
            "filename": filename,
            "caption": caption,
            "content_type": content_type,
            "duration": duration,
            "width": width,
            "height": height,
        }
        self.videos.append(message)
        return message

    async def send_animation(
        self,
        chat_id: str | int,
        animation: bytes,
        *,
        filename: str,
        caption: str | None = None,
        content_type: str | None = None,
        duration: int | None = None,
        width: int | None = None,
        height: int | None = None,
    ) -> dict[str, Any]:
        self.next_message_id += 1
        message = {
            "message_id": self.next_message_id,
            "chat": {"id": chat_id},
            "animation": animation,
            "filename": filename,
            "caption": caption,
            "content_type": content_type,
            "duration": duration,
            "width": width,
            "height": height,
        }
        self.animations.append(message)
        return message

    async def send_audio(
        self,
        chat_id: str | int,
        audio: bytes,
        *,
        filename: str,
        caption: str | None = None,
        content_type: str | None = None,
        duration: int | None = None,
        performer: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        self.next_message_id += 1
        message = {
            "message_id": self.next_message_id,
            "chat": {"id": chat_id},
            "audio": audio,
            "filename": filename,
            "caption": caption,
            "content_type": content_type,
            "duration": duration,
            "performer": performer,
            "title": title,
        }
        self.audios.append(message)
        return message

    async def send_voice(
        self,
        chat_id: str | int,
        voice: bytes,
        *,
        filename: str,
        caption: str | None = None,
        content_type: str | None = None,
        duration: int | None = None,
    ) -> dict[str, Any]:
        self.next_message_id += 1
        message = {
            "message_id": self.next_message_id,
            "chat": {"id": chat_id},
            "voice": voice,
            "filename": filename,
            "caption": caption,
            "content_type": content_type,
            "duration": duration,
        }
        self.voices.append(message)
        return message

    async def send_video_note(
        self,
        chat_id: str | int,
        video_note: bytes,
        *,
        filename: str,
        content_type: str | None = None,
        duration: int | None = None,
        length: int | None = None,
    ) -> dict[str, Any]:
        self.next_message_id += 1
        message = {
            "message_id": self.next_message_id,
            "chat": {"id": chat_id},
            "video_note": video_note,
            "filename": filename,
            "content_type": content_type,
            "duration": duration,
            "length": length,
        }
        self.video_notes.append(message)
        return message

    async def send_sticker(
        self,
        chat_id: str | int,
        sticker: bytes,
        *,
        filename: str,
        content_type: str | None = None,
        emoji: str | None = None,
    ) -> dict[str, Any]:
        self.next_message_id += 1
        message = {
            "message_id": self.next_message_id,
            "chat": {"id": chat_id},
            "sticker": sticker,
            "filename": filename,
            "content_type": content_type,
            "emoji": emoji,
        }
        self.stickers.append(message)
        return message

    async def send_live_photo(
        self,
        chat_id: str | int,
        live_photo: bytes,
        photo: bytes,
        *,
        live_photo_filename: str,
        photo_filename: str,
        caption: str | None = None,
        live_photo_content_type: str | None = None,
        photo_content_type: str | None = None,
    ) -> dict[str, Any]:
        self.next_message_id += 1
        message = {
            "message_id": self.next_message_id,
            "chat": {"id": chat_id},
            "live_photo": live_photo,
            "photo": photo,
            "live_photo_filename": live_photo_filename,
            "photo_filename": photo_filename,
            "caption": caption,
            "live_photo_content_type": live_photo_content_type,
            "photo_content_type": photo_content_type,
        }
        self.live_photos.append(message)
        return message

    async def send_media_group(
        self,
        chat_id: str | int,
        media: list[dict[str, Any]],
        *,
        files: dict[str, tuple[str, bytes, str | None]] | None = None,
    ) -> list[dict[str, Any]]:
        messages = []
        for item in media:
            self.next_message_id += 1
            messages.append({"message_id": self.next_message_id, "chat": {"id": chat_id}, "media": item})
        self.media_groups.append({"chat_id": chat_id, "media": media, "files": files or {}})
        return messages

    async def send_paid_media(
        self,
        chat_id: str | int,
        star_count: int,
        media: list[dict[str, Any]],
        *,
        caption: str | None = None,
        payload: str | None = None,
        files: dict[str, tuple[str, bytes, str | None]] | None = None,
    ) -> dict[str, Any]:
        self.next_message_id += 1
        message = {
            "message_id": self.next_message_id,
            "chat": {"id": chat_id},
            "star_count": star_count,
            "media": media,
            "caption": caption,
            "payload": payload,
            "files": files or {},
        }
        self.paid_media.append(message)
        return message

    async def send_contact(self, chat_id: str | int, phone_number: str, first_name: str, **kwargs: Any) -> dict[str, Any]:
        self.next_message_id += 1
        message = {"message_id": self.next_message_id, "chat": {"id": chat_id}, "phone_number": phone_number, "first_name": first_name, **kwargs}
        self.contacts.append(message)
        return message

    async def send_location(self, chat_id: str | int, latitude: float, longitude: float, **kwargs: Any) -> dict[str, Any]:
        self.next_message_id += 1
        message = {"message_id": self.next_message_id, "chat": {"id": chat_id}, "latitude": latitude, "longitude": longitude, **kwargs}
        self.locations.append(message)
        return message

    async def send_venue(
        self,
        chat_id: str | int,
        latitude: float,
        longitude: float,
        title: str,
        address: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.next_message_id += 1
        message = {
            "message_id": self.next_message_id,
            "chat": {"id": chat_id},
            "latitude": latitude,
            "longitude": longitude,
            "title": title,
            "address": address,
            **kwargs,
        }
        self.venues.append(message)
        return message

    async def send_poll(
        self,
        chat_id: str | int,
        question: str,
        options: list[Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.next_message_id += 1
        normalized_options = [
            {"text": str(option), "voter_count": 0}
            if not isinstance(option, dict)
            else {"voter_count": 0, **option}
            for option in options
        ]
        poll = {
            "id": f"poll_{self.next_message_id}",
            "question": question,
            "options": normalized_options,
            "total_voter_count": 0,
            "is_anonymous": kwargs.get("is_anonymous", True),
            "type": kwargs.get("type") or "regular",
            "allows_multiple_answers": kwargs.get("allows_multiple_answers", False),
            "is_closed": kwargs.get("is_closed", False),
        }
        message = {"message_id": self.next_message_id, "chat": {"id": chat_id}, "poll": poll}
        self.polls.append(message)
        return message

    async def send_checklist(
        self,
        chat_id: str | int,
        business_connection_id: str,
        checklist: dict[str, Any],
    ) -> dict[str, Any]:
        self.next_message_id += 1
        message = {
            "message_id": self.next_message_id,
            "chat": {"id": chat_id},
            "business_connection_id": business_connection_id,
            "checklist": checklist,
        }
        self.checklists.append(message)
        return message

    async def send_dice(self, chat_id: str | int, *, emoji: str | None = None) -> dict[str, Any]:
        self.next_message_id += 1
        resolved_emoji = emoji or "🎲"
        message = {
            "message_id": self.next_message_id,
            "chat": {"id": chat_id},
            "dice": {"emoji": resolved_emoji, "value": 3},
        }
        self.dice.append(message)
        return message

    async def copy_message(
        self,
        chat_id: str | int,
        from_chat_id: str | int,
        message_id: int,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.next_message_id += 1
        message = {
            "message_id": self.next_message_id,
            "chat_id": chat_id,
            "from_chat_id": from_chat_id,
            "source_message_id": message_id,
            **kwargs,
        }
        self.copied_messages.append(message)
        return message

    async def forward_message(
        self,
        chat_id: str | int,
        from_chat_id: str | int,
        message_id: int,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.next_message_id += 1
        message = {
            "message_id": self.next_message_id,
            "chat_id": chat_id,
            "from_chat_id": from_chat_id,
            "source_message_id": message_id,
            **kwargs,
        }
        self.forwarded_messages.append(message)
        return message

    async def edit_message_text(self, chat_id: str | int, message_id: int, text: str, **kwargs: Any) -> bool:
        self.edits.append({"chat_id": chat_id, "message_id": message_id, "text": text, **kwargs})
        return True

    async def answer_callback_query(self, callback_query_id: str, **kwargs: Any) -> bool:
        self.answers.append({"callback_query_id": callback_query_id, **kwargs})
        return True

    async def get_file(self, file_id: str) -> dict[str, Any]:
        return self.files[file_id]

    async def download_file(self, file_path: str) -> bytes:
        return self.downloads[file_path]

    async def set_message_reaction(self, chat_id: str | int, message_id: int, emoji: str) -> bool:
        if not self.supports_reactions:
            raise RuntimeError("reactions unavailable")
        self.reactions.append({"chat_id": chat_id, "message_id": message_id, "emoji": emoji})
        return True


class TypingFakeBot(FakeBot):
    def __init__(self) -> None:
        super().__init__()
        self.chat_actions: list[dict[str, Any]] = []

    async def send_chat_action(self, chat_id: str | int, action: str) -> bool:
        self.chat_actions.append({"chat_id": chat_id, "action": action})
        return True


class FakeAppServer:
    def __init__(self) -> None:
        self.thread_starts: list[dict[str, Any]] = []
        self.thread_resumes: list[dict[str, Any]] = []
        self.thread_forks: list[dict[str, Any]] = []
        self.thread_archives: list[dict[str, Any]] = []
        self.thread_unarchives: list[dict[str, Any]] = []
        self.thread_rollbacks: list[dict[str, Any]] = []
        self.thread_background_cleans: list[dict[str, Any]] = []
        self.thread_loaded_lists: list[dict[str, Any]] = []
        self.guardian_denied_approvals: list[dict[str, Any]] = []
        self.memory_mode_sets: list[dict[str, Any]] = []
        self.thread_settings_updates: list[dict[str, Any]] = []
        self.thread_names: list[dict[str, Any]] = []
        self.goal_sets: list[dict[str, Any]] = []
        self.goal_gets: list[dict[str, Any]] = []
        self.goal_clears: list[dict[str, Any]] = []
        self.thread_lists: list[dict[str, Any]] = []
        self.thread_reads: list[dict[str, Any]] = []
        self.compactions: list[dict[str, Any]] = []
        self.reviews: list[dict[str, Any]] = []
        self.skills: list[dict[str, Any]] = []
        self.turn_starts: list[dict[str, Any]] = []
        self.turn_interrupts: list[dict[str, Any]] = []
        self.turn_steers: list[dict[str, Any]] = []
        self.approval_decisions: list[dict[str, Any]] = []
        self.permission_approval_responses: list[dict[str, Any]] = []
        self.mcp_elicitation_responses: list[dict[str, Any]] = []
        self.user_input_responses: list[dict[str, Any]] = []
        self.tool_results: list[dict[str, Any]] = []
        self.error_responses: list[dict[str, Any]] = []
        self.model_lists: list[dict[str, Any]] = []
        self.permission_profile_lists: list[dict[str, Any]] = []
        self.mode_lists: list[dict[str, Any]] = []
        self.feature_lists: list[dict[str, Any]] = []
        self.feature_enablement_sets: list[dict[str, Any]] = []
        self.skills_config_writes: list[dict[str, Any]] = []
        self.account_reads: list[dict[str, Any]] = []
        self.account_rate_limit_reads: list[dict[str, Any]] = []
        self.hook_lists: list[dict[str, Any]] = []
        self.mcp_status_lists: list[dict[str, Any]] = []
        self.mcp_reloads: list[dict[str, Any]] = []
        self.app_lists: list[dict[str, Any]] = []
        self.plugin_lists: list[dict[str, Any]] = []
        self.config_reads: list[dict[str, Any]] = []
        self.thread_counter = 0
        self.turn_counter = 0
        self.thread_list_result: dict[str, Any] = {"data": []}
        self.thread_list_error: Exception | None = None
        self.goal_result: dict[str, Any] = {"goal": {"objective": "Ship commands", "status": "active"}}

    async def thread_start(self, **kwargs: Any) -> dict[str, Any]:
        self.thread_counter += 1
        self.thread_starts.append(kwargs)
        return {"thread": {"id": f"thr_{self.thread_counter}"}}

    async def thread_resume(self, **kwargs: Any) -> dict[str, Any]:
        self.thread_resumes.append(kwargs)
        return {"thread": {"id": kwargs["thread_id"]}}

    async def thread_fork(self, **kwargs: Any) -> dict[str, Any]:
        self.thread_counter += 1
        self.thread_forks.append(kwargs)
        return {"thread": {"id": f"thr_{self.thread_counter}"}}

    async def thread_archive(self, **kwargs: Any) -> dict[str, Any]:
        self.thread_archives.append(kwargs)
        return {}

    async def thread_unarchive(self, **kwargs: Any) -> dict[str, Any]:
        self.thread_unarchives.append(kwargs)
        return {}

    async def thread_rollback(self, **kwargs: Any) -> dict[str, Any]:
        self.thread_rollbacks.append(kwargs)
        return {}

    async def thread_background_terminals_clean(self, **kwargs: Any) -> dict[str, Any]:
        self.thread_background_cleans.append(kwargs)
        return {}

    async def thread_loaded_list(self, **kwargs: Any) -> dict[str, Any]:
        self.thread_loaded_lists.append(kwargs)
        return {"data": ["thr_1", "thr_agent"]}

    async def thread_approve_guardian_denied_action(self, **kwargs: Any) -> dict[str, Any]:
        self.guardian_denied_approvals.append(kwargs)
        return {}

    async def thread_settings_update(self, **kwargs: Any) -> dict[str, Any]:
        self.thread_settings_updates.append(kwargs)
        return {}

    async def thread_set_name(self, **kwargs: Any) -> dict[str, Any]:
        self.thread_names.append(kwargs)
        return {}

    async def thread_goal_set(self, **kwargs: Any) -> dict[str, Any]:
        self.goal_sets.append(kwargs)
        return {"goal": {"objective": kwargs.get("objective"), "status": kwargs.get("status")}}

    async def thread_goal_get(self, **kwargs: Any) -> dict[str, Any]:
        self.goal_gets.append(kwargs)
        return self.goal_result

    async def thread_goal_clear(self, **kwargs: Any) -> dict[str, Any]:
        self.goal_clears.append(kwargs)
        return {}

    async def thread_list(self, **kwargs: Any) -> dict[str, Any]:
        self.thread_lists.append(kwargs)
        if self.thread_list_error is not None:
            raise self.thread_list_error
        return self.thread_list_result

    async def thread_read(self, **kwargs: Any) -> dict[str, Any]:
        self.thread_reads.append(kwargs)
        if kwargs["thread_id"] == "thr_agent":
            return {
                "thread": {
                    "id": "thr_agent",
                    "title": "Agent work",
                    "cwd": str(Path.cwd()),
                    "status": {"type": "idle"},
                    "agentNickname": "reviewer",
                    "agentRole": "review",
                    "source": {"subAgent": "review"},
                    "turns": [],
                }
            }
        return {
            "thread": {
                "id": kwargs["thread_id"],
                "title": "Gateway work",
                "cwd": str(Path.cwd()),
                "status": {"type": "idle"},
                "turns": [
                    {
                        "id": "turn_1",
                        "items": [
                            {"type": "commandExecution", "command": "pytest", "status": "inProgress", "processId": 456}
                        ],
                    }
                ],
            }
        }

    async def thread_compact_start(self, **kwargs: Any) -> dict[str, Any]:
        self.compactions.append(kwargs)
        return {}

    async def review_start(self, **kwargs: Any) -> dict[str, Any]:
        self.turn_counter += 1
        self.reviews.append(kwargs)
        return {"turn": {"id": f"turn_review_{self.turn_counter}"}}

    async def skills_list(self, **kwargs: Any) -> dict[str, Any]:
        self.skills.append(kwargs)
        return {
            "data": [
                {
                    "cwd": kwargs["cwds"][0],
                    "skills": [
                        {
                            "name": "skill-creator",
                            "path": r"C:\Users\gatewayuser\.codex\skills\skill-creator\SKILL.md",
                        }
                    ],
                    "errors": [],
                }
            ]
        }

    async def model_list(self, **kwargs: Any) -> dict[str, Any]:
        self.model_lists.append(kwargs)
        return {
            "data": [
                {
                    "id": "gpt-5.1",
                    "model": "gpt-5.1",
                    "displayName": "GPT 5.1",
                    "description": "Fast coding model",
                    "isDefault": True,
                    "defaultReasoningEffort": "high",
                    "supportedReasoningEfforts": [
                        {"reasoningEffort": "low", "description": "Fast responses with lighter reasoning"},
                        {"reasoningEffort": "medium", "description": "Balances speed and reasoning depth"},
                        {"reasoningEffort": "high", "description": "Greater reasoning depth"},
                        {"reasoningEffort": "xhigh", "description": "Extra high reasoning depth"},
                    ],
                    "hidden": False,
                }
            ]
        }

    async def permission_profile_list(self, **kwargs: Any) -> dict[str, Any]:
        self.permission_profile_lists.append(kwargs)
        return {"data": [{"id": "read-only", "description": "Read-only access"}]}

    async def collaboration_mode_list(self) -> dict[str, Any]:
        self.mode_lists.append({})
        return {"data": [{"name": "plan", "mode": "plan", "model": "gpt-5.1", "reasoning_effort": "high"}]}

    async def experimental_feature_list(self, **kwargs: Any) -> dict[str, Any]:
        self.feature_lists.append(kwargs)
        return {"data": [{"name": "memories", "displayName": "Memories", "enabled": True, "stage": "stable"}]}

    async def experimental_feature_enablement_set(self, **kwargs: Any) -> dict[str, Any]:
        self.feature_enablement_sets.append(kwargs)
        return {}

    async def thread_memory_mode_set(self, **kwargs: Any) -> dict[str, Any]:
        self.memory_mode_sets.append(kwargs)
        return {}

    async def skills_config_write(self, **kwargs: Any) -> dict[str, Any]:
        self.skills_config_writes.append(kwargs)
        return {}

    async def account_read(self, **kwargs: Any) -> dict[str, Any]:
        self.account_reads.append(kwargs)
        return {"requiresOpenaiAuth": False, "account": {"type": "chatgpt", "email": "user@example.com", "planType": "pro"}}

    async def account_rate_limits_read(self) -> dict[str, Any]:
        self.account_rate_limit_reads.append({})
        return {
            "rateLimits": {
                "limitName": "Codex",
                "primary": {"usedPercent": 42, "windowDurationMins": 300},
            }
        }

    async def hooks_list(self, **kwargs: Any) -> dict[str, Any]:
        self.hook_lists.append(kwargs)
        return {"data": [{"cwd": kwargs["cwds"][0], "hooks": [{"key": "lint", "eventName": "preToolUse", "enabled": True}], "errors": [], "warnings": []}]}

    async def mcp_server_status_list(self, **kwargs: Any) -> dict[str, Any]:
        self.mcp_status_lists.append(kwargs)
        return {"data": [{"name": "github", "authStatus": "oAuth", "tools": {"issue": {"name": "issue"}}, "resources": [], "resourceTemplates": []}]}

    async def config_mcp_server_reload(self) -> dict[str, Any]:
        self.mcp_reloads.append({})
        return {}

    async def app_list(self, **kwargs: Any) -> dict[str, Any]:
        self.app_lists.append(kwargs)
        return {"data": [{"id": "github", "name": "GitHub", "description": "Repository tools", "isEnabled": True}]}

    async def plugin_list(self, **kwargs: Any) -> dict[str, Any]:
        self.plugin_lists.append(kwargs)
        return {
            "marketplaces": [
                {
                    "name": "local",
                    "plugins": [
                        {
                            "id": "github",
                            "name": "GitHub",
                            "installed": True,
                            "enabled": True,
                            "interface": {"shortDescription": "Repository tools"},
                        }
                    ],
                }
            ]
        }

    async def config_read(self, **kwargs: Any) -> dict[str, Any]:
        self.config_reads.append(kwargs)
        return {"config": {"model": "gpt-5.1", "approval_policy": "on-request"}, "origins": {}}

    async def turn_start(self, **kwargs: Any) -> dict[str, Any]:
        self.turn_counter += 1
        self.turn_starts.append(kwargs)
        return {"turn": {"id": f"turn_{self.turn_counter}"}}

    async def turn_interrupt(self, **kwargs: Any) -> dict[str, Any]:
        self.turn_interrupts.append(kwargs)
        return {}

    async def turn_steer(self, **kwargs: Any) -> dict[str, Any]:
        self.turn_steers.append(kwargs)
        return {}

    async def send_approval_decision(self, request_id: int | str, decision: str) -> None:
        self.approval_decisions.append({"request_id": request_id, "decision": decision})

    async def send_mcp_elicitation_response(self, request_id: int | str, action: str) -> None:
        self.mcp_elicitation_responses.append({"request_id": request_id, "action": action})

    async def send_permissions_approval_response(self, request_id: int | str, permissions: dict[str, Any]) -> None:
        self.permission_approval_responses.append(
            {"request_id": request_id, "permissions": permissions, "scope": "turn"}
        )

    async def send_tool_user_input_response(
        self,
        request_id: int | str,
        answers: dict[str, list[str]],
    ) -> None:
        self.user_input_responses.append(
            {
                "request_id": request_id,
                "answers": {
                    question_id: {"answers": values}
                    for question_id, values in answers.items()
                },
            }
        )

    async def send_dynamic_tool_result(self, request_id: int | str, content: list[dict[str, Any]]) -> None:
        self.tool_results.append({"request_id": request_id, "content": content})

    async def send_error_response(self, request_id: int | str, message: str, code: int = -32000) -> None:
        self.error_responses.append({"request_id": request_id, "error": message, "code": code})
        self.tool_results.append({"request_id": request_id, "error": message, "code": code})


def settings_for(
    tmp_path: Path,
    root: Path | None = None,
    *,
    permission_profile: str | None = None,
    pair_command_template: str | None = None,
    model: str | None = None,
    model_reasoning_effort: str | None = None,
) -> TelegramSettings:
    workspace_root = root or (tmp_path / "projects")
    workspace_root.mkdir(parents=True, exist_ok=True)
    default_cwd = workspace_root / "codex-gateway"
    default_cwd.mkdir(parents=True, exist_ok=True)
    return TelegramSettings(
        bot_token="token",
        state_dir=tmp_path / "state",
        allowed_roots=(workspace_root.resolve(strict=False),),
        default_cwd=default_cwd.resolve(strict=False),
        app_server_command=("codex", "app-server", "--listen", "stdio://"),
        model=model,
        model_reasoning_effort=model_reasoning_effort,
        sandbox="workspace-write",
        approval_policy="unlessTrusted",
        approval_timeout_seconds=900,
        max_attachment_bytes=25_000_000,
        poll_timeout_seconds=30,
        permission_profile=permission_profile,
        allowed_user_id="123",
        pair_command_template=pair_command_template,
    )


def message_update(text: str, *, chat_id: int = 42, user_id: int = 123, message_id: int = 10) -> dict[str, Any]:
    return {
        "update_id": message_id,
        "message": {
            "message_id": message_id,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": user_id, "username": "gatewayuser"},
            "text": text,
        },
    }


def callback_update(data: str, *, chat_id: int = 42, user_id: int = 123, message_id: int = 1001) -> dict[str, Any]:
    return {
        "update_id": message_id + 1,
        "callback_query": {
            "id": "cb_1",
            "from": {"id": user_id, "username": "gatewayuser"},
            "message": {"message_id": message_id, "chat": {"id": chat_id}},
            "data": data,
        },
    }


def inline_button_map(message: dict[str, Any]) -> dict[str, str]:
    buttons: dict[str, str] = {}
    for row in message["reply_markup"]["inline_keyboard"]:
        for button in row:
            buttons[str(button["text"])] = str(button["callback_data"])
    return buttons


def assert_callback_keyboard_cleared(edit: dict[str, Any]) -> None:
    assert edit["reply_markup"] == {"inline_keyboard": []}


def selection_token(callback_data: str) -> str:
    prefix, _, token = callback_data.partition(":")
    assert prefix == "select"
    assert token
    return token


def fake_app_server_call_counts(app_server: FakeAppServer) -> dict[str, int]:
    return {
        name: len(value)
        for name, value in vars(app_server).items()
        if isinstance(value, list)
    }


async def complete_active_turn(bridge: TelegramBridge, chat_id: str = "42") -> None:
    context = bridge._active_turn_context(chat_id)
    assert context is not None
    await bridge.handle_app_event(
        AppServerEvent(
            "turn/completed",
            {
                "threadId": context.thread_id,
                "turn": {"id": context.turn_id, "status": "completed", "items": []},
            },
        )
    )


async def drain_background_tasks(bridge: TelegramBridge) -> None:
    while bridge.background_tasks:
        await asyncio.gather(*list(bridge.background_tasks))


def elicitation_token(callback_data: str) -> str:
    prefix, token, action = callback_data.split(":", 2)
    assert prefix == "elicitation"
    assert token
    assert action in {"accept", "decline", "cancel"}
    return token


def user_input_token(callback_data: str) -> str:
    prefix, token, _action = callback_data.split(":", 2)
    assert prefix == "userinput"
    assert token
    return token


def bridge_for(
    tmp_path: Path,
    *,
    now: datetime | None = None,
    permission_profile: str | None = None,
    pair_command_template: str | None = None,
    model: str | None = None,
    model_reasoning_effort: str | None = None,
) -> tuple[TelegramBridge, FakeBot, FakeAppServer, TelegramStateStore, AccessManager]:
    settings = settings_for(
        tmp_path,
        permission_profile=permission_profile,
        pair_command_template=pair_command_template,
        model=model,
        model_reasoning_effort=model_reasoning_effort,
    )
    store = TelegramStateStore(settings.state_dir)
    access = AccessManager(
        store,
        now_fn=lambda: now or datetime(2026, 5, 24, tzinfo=timezone.utc),
        allowed_user_id=settings.allowed_user_id,
    )
    bot = FakeBot()
    app_server = FakeAppServer()
    bridge = TelegramBridge(settings, store, access, bot, app_server)
    return bridge, bot, app_server, store, access


def bridge_for_typing(tmp_path: Path) -> tuple[TelegramBridge, TypingFakeBot, FakeAppServer, TelegramStateStore, AccessManager]:
    settings = settings_for(tmp_path)
    store = TelegramStateStore(settings.state_dir)
    access = AccessManager(
        store,
        now_fn=lambda: datetime(2026, 5, 24, tzinfo=timezone.utc),
        allowed_user_id=settings.allowed_user_id,
    )
    bot = TypingFakeBot()
    app_server = FakeAppServer()
    bridge = TelegramBridge(settings, store, access, bot, app_server)
    return bridge, bot, app_server, store, access


UNKNOWN_COMMAND_CONTRACT_EXAMPLES = frozenset({"models", "modes", "permission", "doesnotexist"})
BRIDGE_CONTRACT_COMMAND_NAMES = frozenset(
    {
        "start",
        "help",
        "status",
        "commands",
        "project",
        "projects",
        "workspace",
        "workspace list",
        "workspace set",
        "setcwd",
        "getcwd",
        "searchcwd",
        "reset",
        "clear",
        "new",
        "resume",
        "fork",
        "side",
        "btw",
        "archive",
        "unarchive",
        "diff",
        "read",
        "review",
        "compact",
        "rollback",
        "mention",
        "init",
        "plan",
        "collab",
        "exec",
        "cancel",
        "interrupt",
        "steer",
        "model",
        "permissions",
        "approval",
        "mode",
        "effort",
        "threads",
        "approve",
        "agent",
        "subagents",
        "personality",
        "experimental",
        "memories",
        "plugins",
        "ps",
        "stop",
        "account",
        "limits",
        "hooks",
        "mcp",
        "apps",
        "features",
        "skills",
        "config",
        "debug-config",
        "goal",
        "rename",
        "usage",
        "context",
        *UNSUPPORTED_COMMANDS,
        *UNKNOWN_COMMAND_CONTRACT_EXAMPLES,
    }
)


def test_bridge_contract_cases_cover_registered_and_known_telegram_commands() -> None:
    registry = default_command_registry()
    registered = {
        command.name
        for command in registry.advertised_commands(
            supported_methods=None,
            enable_exec=True,
            advertise_exec=True,
        )
    }
    parser_known = (
        LOCAL_COMMANDS
        | THREAD_COMMANDS
        | APP_SERVER_COMMANDS
        | CODEX_TURN_COMMANDS
        | UNSUPPORTED_COMMANDS
    )
    covered_base_names = {name.partition(" ")[0] for name in BRIDGE_CONTRACT_COMMAND_NAMES}

    assert sorted(registered - covered_base_names) == []
    assert sorted(parser_known - covered_base_names) == []
    assert sorted(covered_base_names - parser_known - UNKNOWN_COMMAND_CONTRACT_EXAMPLES) == []


def test_telegram_server_request_support_table_matches_generated_schema() -> None:
    assert set(TELEGRAM_SERVER_REQUEST_SUPPORT) == set(generated_server_request_methods())
    assert TELEGRAM_SERVER_REQUEST_SUPPORT == {
        "item/commandExecution/requestApproval": "implemented",
        "item/fileChange/requestApproval": "implemented",
        "item/permissions/requestApproval": "implemented",
        "mcpServer/elicitation/request": "implemented",
        "item/tool/requestUserInput": "implemented",
        "item/tool/call": "implemented",
        "account/chatgptAuthTokens/refresh": "unsupported",
        "attestation/generate": "not_negotiated",
        "applyPatchApproval": "not_negotiated",
        "execCommandApproval": "not_negotiated",
    }


@pytest.mark.asyncio
async def test_approved_text_message_starts_thread_and_turn(tmp_path: Path) -> None:
    bridge, _bot, app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")

    await bridge.handle_update(message_update("Reply exactly OK"))

    assert app_server.thread_starts[0]["cwd"] == str(bridge.settings.default_cwd)
    assert app_server.thread_starts[0]["dynamic_tools"][0]["name"] == "telegram_reply"
    assert app_server.thread_starts[0]["developer_instructions"] == TELEGRAM_GATEWAY_DEVELOPER_INSTRUCTIONS
    assert app_server.turn_starts[0]["thread_id"] == "thr_1"
    assert app_server.turn_starts[0]["input_items"] == [{"type": "text", "text": "Reply exactly OK"}]
    assert "developer_instructions" not in app_server.turn_starts[0]
    assert store.load_threads()[TelegramStateStore.thread_key(42, bridge.settings.default_cwd)]["thread_id"] == "thr_1"


@pytest.mark.asyncio
async def test_configured_default_permission_profile_applies_to_new_threads_and_turns(tmp_path: Path) -> None:
    bridge, _bot, app_server, _store, access = bridge_for(tmp_path, permission_profile=":auto-review")
    access.allow_user("123", username="gatewayuser", source="cli")

    await bridge.handle_update(message_update("use configured permissions"))

    assert app_server.thread_starts[0]["permissions"] == ":auto-review"
    assert "sandbox" not in app_server.thread_starts[0]
    assert app_server.turn_starts[0]["permissions"] == ":auto-review"
    assert "sandbox_policy" not in app_server.turn_starts[0]


@pytest.mark.asyncio
async def test_ordinary_message_is_rejected_while_turn_is_active(tmp_path: Path) -> None:
    bridge, bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")

    await bridge.handle_update(message_update("first turn"))
    await bridge.handle_update(message_update("second turn", message_id=11))

    assert len(app_server.turn_starts) == 1
    assert "A Codex turn is already active" in bot.messages[-1]["text"]
    assert "/steer" in bot.messages[-1]["text"]
    assert "/cancel" in bot.messages[-1]["text"]


@pytest.mark.asyncio
async def test_stale_active_turn_is_recovered_before_rejecting_message(tmp_path: Path) -> None:
    bridge, bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")

    async def read_completed_thread(**kwargs: Any) -> dict[str, Any]:
        app_server.thread_reads.append(kwargs)
        return {
            "thread": {
                "id": kwargs["thread_id"],
                "status": {"type": "idle"},
                "turns": [{"id": "turn_1", "status": "completed", "items": []}],
            }
        }

    app_server.thread_read = read_completed_thread

    await bridge.handle_update(message_update("first turn"))
    context = bridge._active_turn_context("42")
    assert context is not None
    context.last_progress_at = access.now_fn() - timedelta(seconds=301)
    await bridge.handle_update(message_update("second turn", message_id=11))

    assert bridge.turns["turn_1"].completed is True
    assert len(app_server.turn_starts) == 2
    assert app_server.turn_starts[-1]["input_items"] == [{"type": "text", "text": "second turn"}]
    assert not any("A Codex turn is already active" in message["text"] for message in bot.messages)
    assert [message["text"] for message in bot.messages] == [
        "This turn has not shown progress recently. Checking its status.",
        "Recovered the completed turn state. You can send the next message.",
    ]


@pytest.mark.parametrize(
    "command_text",
    [
        "/new",
        "/resume",
        "/fork",
        "/side",
        "/btw",
        "/archive",
        "/unarchive",
        "/rollback",
        "/rename blocked",
        "/review",
        "/compact",
        "/init",
        "/mention README.md",
        "/read README.md",
        "/plan blocked",
        "/collab",
        "/exec python -V",
        "/model",
        "/permissions",
        "/approval never",
        "/mode plan",
        "/effort high",
        "/personality pragmatic",
        "/experimental",
        "/memories disabled",
        "/skills",
        "/stop",
        "/setcwd .",
        "/workspace",
        "/reset",
        "/clear",
    ],
)
@pytest.mark.asyncio
async def test_disabled_commands_are_guarded_while_turn_is_active(tmp_path: Path, command_text: str) -> None:
    bridge, bot, app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")

    await bridge.handle_update(message_update("first turn"))
    before_calls = fake_app_server_call_counts(app_server)
    before_threads = store.load_threads()
    command_name = command_text.split()[0].lstrip("/")

    await bridge.handle_update(message_update(command_text, message_id=11))

    assert bot.messages[-1]["text"] == (
        f"/{command_name} is disabled while a task is in progress. Use /steer <text> or /cancel."
    )
    assert fake_app_server_call_counts(app_server) == before_calls
    assert store.load_threads() == before_threads


@pytest.mark.parametrize("command_text", ["/status", "/threads", "/ps", "/diff"])
@pytest.mark.asyncio
async def test_read_only_commands_still_work_while_turn_is_active(tmp_path: Path, command_text: str) -> None:
    bridge, bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")

    await bridge.handle_update(message_update("first turn"))
    await bridge.handle_update(message_update(command_text, message_id=11))

    assert not bot.messages[-1]["text"].startswith(f"{command_text} is disabled while a task is in progress.")
    assert len(app_server.turn_starts) == 1


@pytest.mark.asyncio
async def test_typing_chat_action_runs_during_turn_and_stops_on_completion(tmp_path: Path) -> None:
    bridge, bot, _app_server, _store, access = bridge_for_typing(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")

    await bridge.handle_update(message_update("Reply exactly OK"))
    await asyncio.sleep(0)

    assert bot.chat_actions == [{"chat_id": 42, "action": "typing"}]
    assert set(bridge.typing_tasks) == {"turn_1"}

    await bridge.handle_app_event(
        AppServerEvent(
            "item/completed",
            {"threadId": "thr_1", "turnId": "turn_1", "item": {"type": "agentMessage", "text": "OK"}},
        )
    )
    await bridge.handle_app_event(
        AppServerEvent(
            "turn/completed",
            {"threadId": "thr_1", "turn": {"id": "turn_1", "status": "completed", "items": []}},
        )
    )

    assert bridge.typing_tasks == {}
    assert [message["text"] for message in bot.messages] == ["OK"]


@pytest.mark.asyncio
async def test_typing_chat_action_stops_when_approval_is_requested(tmp_path: Path) -> None:
    bridge, bot, _app_server, _store, access = bridge_for_typing(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")

    await bridge.handle_update(message_update("run tests"))
    await asyncio.sleep(0)
    await bridge.handle_app_server_request(
        AppServerEvent(
            "item/commandExecution/requestApproval",
            {"threadId": "thr_1", "turnId": "turn_1", "command": "pytest"},
            request_id=77,
        )
    )

    assert bot.chat_actions == [{"chat_id": 42, "action": "typing"}]
    assert bridge.typing_tasks == {}
    assert "Approval requested" in bot.messages[-1]["text"]


@pytest.mark.asyncio
async def test_typing_chat_action_resumes_after_approval_accept(tmp_path: Path) -> None:
    bridge, bot, app_server, store, access = bridge_for_typing(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")

    await bridge.handle_update(message_update("run tests"))
    await asyncio.sleep(0)
    await bridge.handle_app_server_request(
        AppServerEvent(
            "item/commandExecution/requestApproval",
            {"threadId": "thr_1", "turnId": "turn_1", "command": "pytest"},
            request_id=77,
        )
    )
    token = next(iter(store.load_pending_approvals()))

    await bridge.handle_update(callback_update(f"approval:{token}:accept", message_id=bot.messages[-1]["message_id"]))
    await asyncio.sleep(0)

    assert app_server.approval_decisions == [{"request_id": 77, "decision": "accept"}]
    assert set(bridge.typing_tasks) == {"turn_1"}
    assert bot.chat_actions[-1] == {"chat_id": 42, "action": "typing"}


@pytest.mark.asyncio
async def test_existing_thread_mapping_resumes_before_turn_start(tmp_path: Path) -> None:
    bridge, _bot, app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    store.save_threads(
        {
            TelegramStateStore.thread_key(42, bridge.settings.default_cwd): {
                "thread_id": "thr_existing",
                "workspace": str(bridge.settings.default_cwd),
                "created_at": "2026-05-24T00:00:00Z",
                "updated_at": "2026-05-24T00:00:00Z",
                "dynamic_tools_fingerprint": _dynamic_tools_fingerprint(),
            }
        }
    )

    await bridge.handle_update(message_update("continue"))

    assert app_server.thread_resumes[0]["thread_id"] == "thr_existing"
    assert app_server.thread_resumes[0]["sandbox"] == "workspace-write"
    assert app_server.thread_resumes[0]["approval_policy"] == "on-request"
    assert app_server.thread_resumes[0]["developer_instructions"] == TELEGRAM_GATEWAY_DEVELOPER_INSTRUCTIONS
    assert app_server.turn_starts[0]["thread_id"] == "thr_existing"
    assert app_server.thread_starts == []


@pytest.mark.asyncio
async def test_configured_default_permission_profile_applies_to_resumed_threads(tmp_path: Path) -> None:
    bridge, _bot, app_server, store, access = bridge_for(tmp_path, permission_profile=":auto-review")
    access.allow_user("123", username="gatewayuser", source="cli")
    store.save_threads(
        {
            TelegramStateStore.thread_key(42, bridge.settings.default_cwd): {
                "thread_id": "thr_existing",
                "workspace": str(bridge.settings.default_cwd),
                "dynamic_tools_fingerprint": _dynamic_tools_fingerprint(),
            }
        }
    )

    await bridge.handle_update(message_update("continue"))

    assert app_server.thread_resumes[0]["permissions"] == ":auto-review"
    assert app_server.thread_resumes[0]["sandbox"] is None
    assert app_server.thread_resumes[0]["developer_instructions"] == TELEGRAM_GATEWAY_DEVELOPER_INSTRUCTIONS
    assert app_server.turn_starts[0]["permissions"] == ":auto-review"
    assert "sandbox_policy" not in app_server.turn_starts[0]


@pytest.mark.asyncio
async def test_stale_thread_mapping_starts_replacement_thread_when_resume_fails(tmp_path: Path) -> None:
    bridge, _bot, app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    store.save_threads(
        {
            TelegramStateStore.thread_key(42, bridge.settings.default_cwd): {
                "thread_id": "thr_stale",
                "workspace": str(bridge.settings.default_cwd),
                "created_at": "2026-05-24T00:00:00Z",
                "updated_at": "2026-05-24T00:00:00Z",
                "dynamic_tools_fingerprint": _dynamic_tools_fingerprint(),
            }
        }
    )

    async def fail_resume(**_kwargs: Any) -> dict[str, Any]:
        raise JsonRpcError("no rollout found for thread id thr_stale")

    app_server.thread_resume = fail_resume

    await bridge.handle_update(message_update("continue"))

    assert app_server.thread_starts[0]["cwd"] == str(bridge.settings.default_cwd)
    assert app_server.turn_starts[0]["thread_id"] == "thr_1"
    assert store.load_threads()[TelegramStateStore.thread_key(42, bridge.settings.default_cwd)]["thread_id"] == "thr_1"


@pytest.mark.asyncio
async def test_normal_message_app_server_failure_reports_without_crashing(tmp_path: Path) -> None:
    bridge, bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")

    async def fail_turn_start(**_kwargs: Any) -> dict[str, Any]:
        raise JsonRpcError("App-server transport send failed: closed")

    app_server.turn_start = fail_turn_start

    await bridge.handle_update(message_update("continue"))

    assert bot.messages[-1]["text"] == "App-server command failed: App-server transport send failed: closed"


@pytest.mark.asyncio
async def test_thread_mapping_with_stale_dynamic_tools_starts_replacement_thread(tmp_path: Path) -> None:
    bridge, _bot, app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    store.save_threads(
        {
            TelegramStateStore.thread_key(42, bridge.settings.default_cwd): {
                "thread_id": "thr_old_tools",
                "workspace": str(bridge.settings.default_cwd),
                "created_at": "2026-05-24T00:00:00Z",
                "updated_at": "2026-05-24T00:00:00Z",
                "dynamic_tools_fingerprint": "old",
            }
        }
    )

    await bridge.handle_update(message_update("continue"))

    assert app_server.thread_resumes == []
    assert app_server.thread_starts[0]["dynamic_tools"] == telegram_dynamic_tools()
    assert app_server.turn_starts[0]["thread_id"] == "thr_1"
    record = store.load_threads()[TelegramStateStore.thread_key(42, bridge.settings.default_cwd)]
    assert record["thread_id"] == "thr_1"
    assert record["dynamic_tools_fingerprint"] == _dynamic_tools_fingerprint()


@pytest.mark.asyncio
async def test_unpaired_configured_user_gets_start_guidance_without_app_server_call(tmp_path: Path) -> None:
    bridge, bot, app_server, store, _access = bridge_for(tmp_path)

    await bridge.handle_update(message_update("hello", user_id=123))

    message = bot.messages[-1]["text"]
    assert "not paired" in message
    assert "Send /start" in message
    assert store.load_access()["pairing_codes"] == {}
    assert app_server.thread_starts == []
    assert app_server.turn_starts == []


@pytest.mark.asyncio
async def test_start_creates_pairing_command_for_configured_unpaired_user(tmp_path: Path) -> None:
    bridge, bot, app_server, store, _access = bridge_for(tmp_path)

    await bridge.handle_update(message_update("/start", user_id=123))

    message = bot.messages[-1]["text"]
    assert "not paired" in message
    match = re.search(r"uv run codex-gateway telegram access pair ([A-Z0-9]{4}-[A-Z0-9]{4})", message)
    assert match is not None
    pairing = store.load_access()["pairing_codes"][match.group(1)]
    assert pairing["user_id"] == "123"
    assert pairing["chat_id"] == "42"
    assert app_server.thread_starts == []
    assert app_server.turn_starts == []


@pytest.mark.asyncio
async def test_start_uses_configured_pairing_command_template(tmp_path: Path) -> None:
    bridge, bot, _app_server, _store, _access = bridge_for(
        tmp_path,
        pair_command_template=(
            "docker compose -f testing\\docker\\compose.linux.yaml run --rm "
            "codex-gateway-cli pair {code}"
        ),
    )

    await bridge.handle_update(message_update("/start", user_id=123))

    message = bot.messages[-1]["text"]
    match = re.search(
        r"docker compose -f testing\\docker\\compose\.linux\.yaml run --rm "
        r"codex-gateway-cli pair ([A-Z0-9]{4}-[A-Z0-9]{4})",
        message,
    )
    assert match is not None


@pytest.mark.asyncio
async def test_unconfigured_user_is_rejected_without_pairing_code(tmp_path: Path) -> None:
    bridge, bot, app_server, store, _access = bridge_for(tmp_path)

    await bridge.handle_update(message_update("/start", user_id=999))

    assert "not authorized" in bot.messages[-1]["text"]
    assert store.load_access()["pairing_codes"] == {}
    assert app_server.thread_starts == []
    assert app_server.turn_starts == []


@pytest.mark.parametrize(
    "command_text",
    [
        "/start",
        "/help",
        "/status",
        "/commands",
        "/project",
        "/projects",
        "/workspace",
        "/workspace list",
        "/setcwd",
        "/getcwd",
        "/searchcwd codex",
        "/reset",
        "/clear",
        "/diff",
    ],
)
@pytest.mark.asyncio
async def test_local_slash_commands_send_valid_bot_messages_without_app_server_calls(
    tmp_path: Path,
    command_text: str,
) -> None:
    bridge, bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")

    await bridge.handle_update(message_update(command_text))

    assert bot.messages
    assert bot.messages[-1]["chat"]["id"] == 42
    assert isinstance(bot.messages[-1]["text"], str)
    assert bot.messages[-1]["text"].strip()
    assert app_server.thread_starts == []
    assert app_server.turn_starts == []


@pytest.mark.parametrize("command_text", ["/help", "/start"])
@pytest.mark.asyncio
async def test_help_and_start_show_grouped_command_reference(tmp_path: Path, command_text: str) -> None:
    bridge, bot, _app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")

    await bridge.handle_update(message_update(command_text))

    text = bot.messages[-1]["text"]
    for heading in [
        "Basics",
        "Workspace",
        "Threads",
        "Turns",
        "Settings and Tools",
        "Typed-only aliases",
    ]:
        assert heading in text
    assert "/permissions - choose a permission profile" in text
    assert re.search(r"/permission(?:\s|$)", text) is None


@pytest.mark.parametrize(
    ("command_text", "expected_prompt"),
    [
        ("/mention README.md", "Use the referenced path in this task: README.md"),
        ("/read README.md", "Read and summarize this path: README.md"),
        ("/compact", "Compact this conversation context."),
        ("/collab", "Show the current collaboration mode."),
    ],
)
@pytest.mark.asyncio
async def test_codex_turn_slash_commands_start_valid_turn_requests(
    tmp_path: Path,
    command_text: str,
    expected_prompt: str,
) -> None:
    bridge, _bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")

    await bridge.handle_update(message_update(command_text))

    assert app_server.thread_starts[0]["cwd"] == str(bridge.settings.default_cwd)
    assert app_server.turn_starts[0]["thread_id"] == "thr_1"
    assert app_server.turn_starts[0]["input_items"] == [{"type": "text", "text": expected_prompt}]


@pytest.mark.asyncio
async def test_rollback_uses_app_server_thread_history_rollback(tmp_path: Path) -> None:
    bridge, bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")

    await bridge.handle_update(message_update("hi"))
    await complete_active_turn(bridge)
    await bridge.handle_update(message_update("/rollback 2", message_id=11))

    assert app_server.thread_rollbacks == [{"thread_id": "thr_1", "num_turns": 2}]
    assert bot.messages[-1]["text"] == "Thread history rolled back."


@pytest.mark.asyncio
async def test_model_approval_permission_mode_goal_and_rename_commands_update_thread_settings(tmp_path: Path) -> None:
    bridge, bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")

    await bridge.handle_update(message_update("/model gpt-5.1 high"))
    await bridge.handle_update(message_update("/approval never", message_id=11))
    await bridge.handle_update(message_update("/permissions read-only", message_id=12))
    await bridge.handle_update(message_update("/mode plan", message_id=13))
    await bridge.handle_update(message_update("/goal set Ship Telegram commands", message_id=15))
    await bridge.handle_update(message_update("/goal", message_id=16))
    await bridge.handle_update(message_update("/rename Gateway work", message_id=17))
    await bridge.handle_update(message_update("/goal clear", message_id=18))
    await bridge.handle_update(message_update("use saved settings", message_id=19))

    assert app_server.thread_starts[0]["cwd"] == str(bridge.settings.default_cwd)
    assert app_server.thread_settings_updates[0] == {"thread_id": "thr_1", "model": "gpt-5.1", "effort": "high"}
    assert app_server.thread_settings_updates[1] == {"thread_id": "thr_1", "approval_policy": "never"}
    assert app_server.thread_settings_updates[2] == {
        "thread_id": "thr_1",
        "permissions": "read-only",
        "approval_policy": "on-request",
    }
    assert app_server.thread_settings_updates[3] == {
        "thread_id": "thr_1",
        "collaboration_mode": {
            "mode": "plan",
            "settings": {
                "developer_instructions": None,
                "model": "gpt-5.1",
                "reasoning_effort": "high",
            },
        },
    }
    assert app_server.goal_sets == [{"thread_id": "thr_1", "objective": "Ship Telegram commands", "status": "active"}]
    assert app_server.goal_gets == [{"thread_id": "thr_1"}]
    assert app_server.goal_clears == [{"thread_id": "thr_1"}]
    assert app_server.thread_names == [{"thread_id": "thr_1", "name": "Gateway work"}]
    assert app_server.turn_starts[-1]["approval_policy"] == "on-request"
    assert app_server.turn_starts[-1]["permissions"] == "read-only"
    assert "sandbox_policy" not in app_server.turn_starts[-1]
    assert any("Goal:" in message["text"] for message in bot.messages)


@pytest.mark.asyncio
async def test_cli_parity_app_server_commands_and_callbacks(tmp_path: Path) -> None:
    bridge, bot, app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")

    await bridge.handle_update(message_update("hi"))
    await complete_active_turn(bridge)
    await bridge.handle_update(message_update("/personality pragmatic", message_id=11))
    await bridge.handle_update(message_update("/memories disabled", message_id=12))
    await bridge.handle_update(message_update("/plugins", message_id=13))
    await bridge.handle_update(message_update("/ps", message_id=14))

    assert {"thread_id": "thr_1", "personality": "pragmatic"} in app_server.thread_settings_updates
    assert app_server.memory_mode_sets == [{"thread_id": "thr_1", "mode": "disabled"}]
    assert app_server.plugin_lists == [{"cwds": [str(bridge.settings.default_cwd)]}]
    assert "pytest" in bot.messages[-1]["text"]

    await bridge.handle_update(message_update("/experimental", message_id=15))
    buttons = inline_button_map(bot.messages[-1])
    await bridge.handle_update(callback_update(buttons["Memories: enabled"], message_id=bot.messages[-1]["message_id"]))
    assert app_server.feature_enablement_sets == [{"enablement": {"memories": False}}]

    await bridge.handle_update(message_update("/skills", message_id=16))
    buttons = inline_button_map(bot.messages[-1])
    await bridge.handle_update(callback_update(buttons["skill-creator: enabled"], message_id=bot.messages[-1]["message_id"]))
    assert app_server.skills_config_writes == [
        {
            "enabled": False,
            "name": None,
            "path": r"C:\Users\gatewayuser\.codex\skills\skill-creator\SKILL.md",
        }
    ]

    await bridge.handle_update(message_update("/stop", message_id=17))
    buttons = inline_button_map(bot.messages[-1])
    await bridge.handle_update(callback_update(buttons["Stop background terminals"], message_id=bot.messages[-1]["message_id"]))
    assert app_server.thread_background_cleans == [{"thread_id": "thr_1"}]

    await bridge.handle_update(message_update("/agent", message_id=18))
    buttons = inline_button_map(bot.messages[-1])
    await bridge.handle_update(callback_update(buttons["reviewer"], message_id=bot.messages[-1]["message_id"]))
    assert store.load_threads()[TelegramStateStore.thread_key(42, bridge.settings.default_cwd)]["thread_id"] == "thr_agent"


@pytest.mark.asyncio
async def test_approve_tracks_guardian_denials_and_approves_selected_action(tmp_path: Path) -> None:
    bridge, bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update("hi"))
    event = {
        "threadId": "thr_1",
        "turnId": "turn_1",
        "reviewId": "review_1",
        "review": {"status": "denied", "riskLevel": "high"},
        "action": {"type": "command", "command": "Remove-Item -Recurse .", "cwd": str(bridge.settings.default_cwd)},
    }

    await bridge.handle_app_event(AppServerEvent("item/autoApprovalReview/completed", event))
    await bridge.handle_update(message_update("/approve", message_id=11))
    buttons = inline_button_map(bot.messages[-1])
    await bridge.handle_update(callback_update(buttons["Remove-Item -Recurse ."], message_id=bot.messages[-1]["message_id"]))

    assert app_server.guardian_denied_approvals == [{"thread_id": "thr_1", "event": event}]


@pytest.mark.asyncio
async def test_mode_command_uses_default_model_when_collaboration_mode_has_no_model(tmp_path: Path) -> None:
    bridge, bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")

    async def live_shape_collaboration_mode_list() -> dict[str, Any]:
        app_server.mode_lists.append({})
        return {
            "data": [
                {"mode": "plan", "model": None, "name": "Plan", "reasoning_effort": "medium"},
                {"mode": "default", "model": None, "name": "Default", "reasoning_effort": None},
            ]
        }

    app_server.collaboration_mode_list = live_shape_collaboration_mode_list

    await bridge.handle_update(message_update("/mode Plan"))

    assert app_server.model_lists == [{}]
    assert app_server.thread_settings_updates == [
        {
            "thread_id": "thr_1",
            "collaboration_mode": {
                "mode": "plan",
                "settings": {
                    "developer_instructions": None,
                    "model": "gpt-5.1",
                    "reasoning_effort": "medium",
                },
            },
        }
    ]
    assert bot.messages[-1]["text"] == "Collaboration mode set to Plan for subsequent turns."


@pytest.mark.asyncio
async def test_plan_command_preserves_built_in_plan_developer_instructions(tmp_path: Path) -> None:
    bridge, _bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")

    await bridge.handle_update(message_update("/plan inspect first"))

    assert app_server.thread_starts[0]["developer_instructions"] == TELEGRAM_GATEWAY_DEVELOPER_INSTRUCTIONS
    assert app_server.thread_settings_updates == [
        {
            "thread_id": "thr_1",
            "collaboration_mode": {
                "mode": "plan",
                "settings": {
                    "developer_instructions": None,
                    "model": "gpt-5.1",
                    "reasoning_effort": "high",
                },
            },
        }
    ]
    assert app_server.turn_starts[0]["input_items"] == [{"type": "text", "text": "inspect first"}]
    assert "developer_instructions" not in app_server.turn_starts[0]


@pytest.mark.asyncio
async def test_model_command_rejects_unknown_model_before_updating_thread(tmp_path: Path) -> None:
    bridge, bot, app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")

    await bridge.handle_update(message_update("/model gpt-5.4-minis"))

    assert app_server.model_lists == [{}]
    assert app_server.thread_settings_updates == []
    assert store.load_threads() == {}
    assert "Unknown model: gpt-5.4-minis" in bot.messages[-1]["text"]
    assert "GPT 5.1" in bot.messages[-1]["text"]


@pytest.mark.asyncio
async def test_model_command_with_model_only_prompts_for_reasoning_effort(tmp_path: Path) -> None:
    bridge, bot, app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")

    await bridge.handle_update(message_update("/model gpt-5.1"))

    assert app_server.model_lists == [{}]
    assert app_server.thread_settings_updates == []
    assert app_server.thread_starts == []
    assert bot.messages[-1]["text"] == "Select reasoning level for gpt-5.1:"
    buttons = inline_button_map(bot.messages[-1])
    assert list(buttons) == ["Low", "Medium", "High (default)", "Extra high", "Cancel"]
    assert len(store.load_pending_selections()) == len(buttons)


@pytest.mark.asyncio
async def test_legacy_effort_command_still_updates_reasoning_effort_without_menu(tmp_path: Path) -> None:
    bridge, bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")

    await bridge.handle_update(message_update("/effort extra high"))

    assert app_server.thread_settings_updates == [{"thread_id": "thr_1", "effort": "xhigh"}]
    assert bot.messages[-1]["text"] == "Reasoning effort set to xhigh for subsequent turns."


@pytest.mark.parametrize(
    ("command_text", "call_attr", "expected_labels", "expected_actions"),
    [
        ("/model", "model_lists", ["gpt-5.1", "Cancel"], {"gpt-5.1": ("model", {"model": "gpt-5.1"}), "Cancel": ("cancel", None)}),
        (
            "/permissions",
            "permission_profile_lists",
            ["Read Only", "Default", "Auto-review", "Full Access", "Cancel"],
            {
                "Read Only": ("permission", "read-only"),
                "Default": ("permission", ":workspace"),
                "Auto-review": ("permission", ":auto-review"),
                "Full Access": ("permission", ":danger-full-access"),
                "Cancel": ("cancel", None),
            },
        ),
        ("/mode", "mode_lists", ["plan", "Cancel"], {"plan": ("mode", "plan"), "Cancel": ("cancel", None)}),
        (
            "/approval",
            "thread_settings_updates",
            ["Untrusted", "On failure", "On request", "Never", "Cancel"],
            {
                "Untrusted": ("approval", "untrusted"),
                "On failure": ("approval", "on-failure"),
                "On request": ("approval", "on-request"),
                "Never": ("approval", "never"),
                "Cancel": ("cancel", None),
            },
        ),
    ],
)
@pytest.mark.asyncio
async def test_no_arg_setting_commands_render_inline_selection_buttons(
    tmp_path: Path,
    command_text: str,
    call_attr: str,
    expected_labels: list[str],
    expected_actions: dict[str, tuple[str, Any]],
) -> None:
    bridge, bot, app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")

    await bridge.handle_update(message_update(command_text))

    if call_attr != "thread_settings_updates":
        assert getattr(app_server, call_attr)
    assert app_server.thread_settings_updates == []
    assert app_server.thread_starts == []
    buttons = inline_button_map(bot.messages[-1])
    assert list(buttons) == expected_labels
    pending = store.load_pending_selections()
    assert len(pending) == len(expected_labels)
    for label, expected in expected_actions.items():
        record = pending[selection_token(buttons[label])]
        assert record["action"] == expected[0]
        expected_value = expected[1]
        if isinstance(expected_value, dict):
            assert isinstance(record.get("value"), dict)
            for key, value in expected_value.items():
                assert record["value"].get(key) == value
        else:
            assert record.get("value") == expected_value
        assert record["chat_id"] == "42"
        assert record["user_id"] == "123"


@pytest.mark.asyncio
async def test_permissions_menu_matches_cli_profiles_without_approval_buttons(tmp_path: Path) -> None:
    bridge, bot, app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")

    async def real_shape_permission_profile_list(**kwargs: Any) -> dict[str, Any]:
        app_server.permission_profile_lists.append(kwargs)
        return {
            "data": [
                {"id": ":read-only", "description": None},
                {"id": ":workspace", "description": None},
                {"id": ":auto-review", "description": None},
                {"id": ":danger-full-access", "description": None},
            ]
        }

    app_server.permission_profile_list = real_shape_permission_profile_list

    await bridge.handle_update(message_update("/permissions"))

    buttons = inline_button_map(bot.messages[-1])
    assert list(buttons) == ["Read Only", "Default", "Auto-review", "Full Access", "Cancel"]
    assert not any(label.startswith("Approval:") for label in buttons)
    pending = store.load_pending_selections()
    expected_values = {
        "Read Only": ":read-only",
        "Default": ":workspace",
        "Auto-review": ":auto-review",
        "Full Access": ":danger-full-access",
    }
    for label, expected_value in expected_values.items():
        record = pending[selection_token(buttons[label])]
        assert record["action"] == "permission"
        assert record["value"] == expected_value


@pytest.mark.asyncio
async def test_empty_model_and_mode_selectors_send_empty_state_without_pending_callbacks(tmp_path: Path) -> None:
    bridge, bot, app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")

    async def empty_model_list(**kwargs: Any) -> dict[str, Any]:
        app_server.model_lists.append(kwargs)
        return {"data": []}

    async def empty_collaboration_mode_list() -> dict[str, Any]:
        app_server.mode_lists.append({})
        return {"data": []}

    app_server.model_list = empty_model_list
    app_server.collaboration_mode_list = empty_collaboration_mode_list

    await bridge.handle_update(message_update("/model"))
    await bridge.handle_update(message_update("/mode", message_id=11))

    assert [message["text"] for message in bot.messages[-2:]] == [
        "No models found.",
        "No collaboration modes found.",
    ]
    assert store.load_pending_selections() == {}
    assert app_server.thread_starts == []
    assert app_server.thread_settings_updates == []


@pytest.mark.parametrize(
    ("command_text", "button_label", "expected_update"),
    [
        (
            "/permissions",
            "Read Only",
            {"thread_id": "thr_1", "permissions": "read-only", "approval_policy": "on-request"},
        ),
        ("/approval", "Never", {"thread_id": "thr_1", "approval_policy": "never"}),
        (
            "/mode",
            "plan",
            {
                "thread_id": "thr_1",
                "collaboration_mode": {
                    "mode": "plan",
                    "settings": {
                        "developer_instructions": None,
                        "model": "gpt-5.1",
                        "reasoning_effort": "high",
                    },
                },
            },
        ),
    ],
)
@pytest.mark.asyncio
async def test_selection_callbacks_apply_each_setting_and_clear_pending_group(
    tmp_path: Path,
    command_text: str,
    button_label: str,
    expected_update: dict[str, Any],
) -> None:
    bridge, bot, app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update(command_text))
    buttons = inline_button_map(bot.messages[-1])

    await bridge.handle_update(callback_update(buttons[button_label], message_id=bot.messages[-1]["message_id"]))

    assert app_server.thread_settings_updates == [expected_update]
    assert store.load_pending_selections() == {}
    assert_callback_keyboard_cleared(bot.edits[-1])
    assert bot.answers[-1]["text"] == "Selection applied."


@pytest.mark.parametrize("command_text", ["/model", "/permissions", "/approval", "/mode"])
@pytest.mark.asyncio
async def test_selection_cancel_clears_each_pending_group_without_app_server_setting(
    tmp_path: Path,
    command_text: str,
) -> None:
    bridge, bot, app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update(command_text))
    buttons = inline_button_map(bot.messages[-1])

    await bridge.handle_update(callback_update(buttons["Cancel"], message_id=bot.messages[-1]["message_id"]))

    assert app_server.thread_starts == []
    assert app_server.thread_settings_updates == []
    assert store.load_pending_selections() == {}
    assert bot.edits[-1]["text"] == "Selection cancelled."
    assert_callback_keyboard_cleared(bot.edits[-1])
    assert bot.answers[-1]["text"] == "Selection cancelled."


@pytest.mark.asyncio
async def test_model_selection_then_reasoning_selection_applies_both_settings(tmp_path: Path) -> None:
    bridge, bot, app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update("/model"))
    buttons = inline_button_map(bot.messages[-1])

    await bridge.handle_update(callback_update(buttons["gpt-5.1"], message_id=bot.messages[-1]["message_id"]))

    assert app_server.thread_settings_updates == []
    assert bot.edits[-1]["text"] == "Model selected: gpt-5.1. Select reasoning level below."
    effort_buttons = inline_button_map(bot.messages[-1])
    assert list(effort_buttons) == ["Low", "Medium", "High (default)", "Extra high", "Cancel"]
    pending = store.load_pending_selections()
    assert len(pending) == len(effort_buttons)

    await bridge.handle_update(
        callback_update(effort_buttons["High (default)"], message_id=bot.messages[-1]["message_id"])
    )

    assert app_server.thread_settings_updates == [{"thread_id": "thr_1", "model": "gpt-5.1", "effort": "high"}]
    assert store.load_pending_selections() == {}
    assert "Model set to gpt-5.1; reasoning effort set to high" in bot.edits[-1]["text"]
    assert bot.answers[-1]["text"] == "Selection applied."


@pytest.mark.asyncio
async def test_saved_model_selection_applies_to_new_threads(tmp_path: Path) -> None:
    bridge, bot, app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")

    await bridge.handle_update(message_update("/model gpt-5.1 high"))
    await bridge.handle_update(message_update("/new", message_id=11))

    key = TelegramStateStore.thread_key(42, bridge.settings.default_cwd)
    record = store.load_threads()[key]
    assert record["thread_id"] == "thr_2"
    assert record["settings"]["active_mode"] == "default"
    assert record["settings"]["modes"]["default"] == {"model": "gpt-5.1", "effort": "high"}
    assert app_server.thread_starts[-1]["model"] == "gpt-5.1"
    assert app_server.thread_settings_updates[-1] == {"thread_id": "thr_2", "effort": "high", "model": "gpt-5.1"}
    assert bot.messages[-1]["text"] == f"Started a new Codex thread for {bridge.settings.default_cwd}."


@pytest.mark.asyncio
async def test_setup_model_preference_applies_to_new_threads(tmp_path: Path) -> None:
    bridge, _bot, app_server, _store, access = bridge_for(
        tmp_path,
        model="gpt-5.4-mini",
        model_reasoning_effort="medium",
    )
    access.allow_user("123", username="gatewayuser", source="cli")

    await bridge.handle_update(message_update("start with configured model"))

    assert app_server.thread_starts[-1]["model"] == "gpt-5.4-mini"
    assert app_server.thread_settings_updates[-1] == {
        "thread_id": "thr_1",
        "effort": "medium",
        "model": "gpt-5.4-mini",
    }


@pytest.mark.asyncio
async def test_model_selection_is_scoped_to_active_collaboration_mode(tmp_path: Path) -> None:
    bridge, _bot, app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")

    async def model_list(**kwargs: Any) -> dict[str, Any]:
        app_server.model_lists.append(kwargs)
        return {
            "data": [
                {
                    "id": "gpt-5.1",
                    "model": "gpt-5.1",
                    "displayName": "GPT 5.1",
                    "supportedReasoningEfforts": [{"reasoningEffort": "medium"}, {"reasoningEffort": "high"}],
                },
                {
                    "id": "gpt-5.1-codex-max",
                    "model": "gpt-5.1-codex-max",
                    "displayName": "GPT 5.1 Codex Max",
                    "supportedReasoningEfforts": [{"reasoningEffort": "medium"}, {"reasoningEffort": "high"}],
                },
            ]
        }

    async def collaboration_mode_list() -> dict[str, Any]:
        app_server.mode_lists.append({})
        return {
            "data": [
                {"name": "default", "mode": "default", "model": "gpt-5.1", "reasoning_effort": "medium"},
                {"name": "plan", "mode": "plan", "model": "gpt-5.1-codex-max", "reasoning_effort": "high"},
            ]
        }

    app_server.model_list = model_list
    app_server.collaboration_mode_list = collaboration_mode_list

    await bridge.handle_update(message_update("/model gpt-5.1 medium"))
    await bridge.handle_update(message_update("/mode plan", message_id=11))
    await bridge.handle_update(message_update("/model gpt-5.1-codex-max high", message_id=12))
    await bridge.handle_update(message_update("/mode default", message_id=13))
    await bridge.handle_update(message_update("/plan inspect", message_id=14))

    key = TelegramStateStore.thread_key(42, bridge.settings.default_cwd)
    settings = store.load_threads()[key]["settings"]
    assert settings["active_mode"] == "plan"
    assert settings["modes"]["default"] == {"model": "gpt-5.1", "effort": "medium"}
    assert settings["modes"]["plan"] == {"model": "gpt-5.1-codex-max", "effort": "high"}
    plan_updates = [
        update["collaboration_mode"]
        for update in app_server.thread_settings_updates
        if update.get("collaboration_mode", {}).get("mode") == "plan"
    ]
    assert plan_updates[-1]["settings"]["model"] == "gpt-5.1-codex-max"
    assert plan_updates[-1]["settings"]["reasoning_effort"] == "high"
    assert app_server.turn_starts[-1]["input_items"] == [{"type": "text", "text": "inspect"}]


@pytest.mark.asyncio
async def test_new_thread_uses_active_plan_mode_model_and_effort(tmp_path: Path) -> None:
    bridge, _bot, app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")

    async def model_list(**kwargs: Any) -> dict[str, Any]:
        app_server.model_lists.append(kwargs)
        return {
            "data": [
                {
                    "id": "gpt-5.1-codex-max",
                    "model": "gpt-5.1-codex-max",
                    "displayName": "GPT 5.1 Codex Max",
                    "supportedReasoningEfforts": [{"reasoningEffort": "high"}],
                }
            ]
        }

    async def collaboration_mode_list() -> dict[str, Any]:
        app_server.mode_lists.append({})
        return {"data": [{"name": "plan", "mode": "plan", "model": "gpt-5.1-codex-max", "reasoning_effort": "high"}]}

    app_server.model_list = model_list
    app_server.collaboration_mode_list = collaboration_mode_list

    await bridge.handle_update(message_update("/mode plan"))
    await bridge.handle_update(message_update("/model gpt-5.1-codex-max high", message_id=11))
    await bridge.handle_update(message_update("/new", message_id=12))

    key = TelegramStateStore.thread_key(42, bridge.settings.default_cwd)
    record = store.load_threads()[key]
    assert record["thread_id"] == "thr_2"
    assert record["settings"]["active_mode"] == "plan"
    assert record["settings"]["modes"]["plan"] == {"model": "gpt-5.1-codex-max", "effort": "high"}
    assert app_server.thread_starts[-1]["model"] == "gpt-5.1-codex-max"
    assert app_server.thread_settings_updates[-1] == {
        "thread_id": "thr_2",
        "effort": "high",
        "model": "gpt-5.1-codex-max",
        "collaboration_mode": {
            "mode": "plan",
            "settings": {
                "developer_instructions": None,
                "model": "gpt-5.1-codex-max",
                "reasoning_effort": "high",
            },
        },
    }


@pytest.mark.asyncio
async def test_stale_replacement_replays_saved_thread_preferences(tmp_path: Path) -> None:
    bridge, _bot, app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    key = TelegramStateStore.thread_key(42, bridge.settings.default_cwd)
    store.save_threads(
        {
            key: {
                "thread_id": "thr_stale",
                "workspace": str(bridge.settings.default_cwd),
                "dynamic_tools_fingerprint": _dynamic_tools_fingerprint(),
                "settings": {
                    "active_mode": "plan",
                    "modes": {
                        "default": {"model": "gpt-5.1", "effort": "medium"},
                        "plan": {"model": "gpt-5.1-codex-max", "effort": "high"},
                    },
                    "permissions": "read-only",
                    "approval_policy": "on-request",
                    "personality": "pragmatic",
                    "memory_mode": "disabled",
                },
            }
        }
    )

    async def fail_resume(**_kwargs: Any) -> dict[str, Any]:
        raise JsonRpcError("no rollout found for thread id thr_stale")

    async def collaboration_mode_list() -> dict[str, Any]:
        app_server.mode_lists.append({})
        return {"data": [{"name": "plan", "mode": "plan", "model": "gpt-5.1-codex-max", "reasoning_effort": "high"}]}

    app_server.thread_resume = fail_resume
    app_server.collaboration_mode_list = collaboration_mode_list

    await bridge.handle_update(message_update("continue"))

    assert app_server.thread_starts[0]["model"] == "gpt-5.1-codex-max"
    assert app_server.thread_starts[0]["permissions"] == "read-only"
    assert app_server.thread_starts[0]["approval_policy"] == "on-request"
    assert app_server.thread_settings_updates[0] == {
        "thread_id": "thr_1",
        "effort": "high",
        "model": "gpt-5.1-codex-max",
        "personality": "pragmatic",
        "collaboration_mode": {
            "mode": "plan",
            "settings": {
                "developer_instructions": None,
                "model": "gpt-5.1-codex-max",
                "reasoning_effort": "high",
            },
        },
    }
    assert app_server.memory_mode_sets == [{"thread_id": "thr_1", "mode": "disabled"}]
    assert app_server.turn_starts[0]["permissions"] == "read-only"
    assert app_server.turn_starts[0]["approval_policy"] == "on-request"


@pytest.mark.asyncio
async def test_clear_preserves_saved_preferences_for_next_thread(tmp_path: Path) -> None:
    bridge, _bot, app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")

    await bridge.handle_update(message_update("/model gpt-5.1 high"))
    await bridge.handle_update(message_update("/clear", message_id=11))

    key = TelegramStateStore.thread_key(42, bridge.settings.default_cwd)
    cleared_record = store.load_threads()[key]
    assert "thread_id" not in cleared_record
    assert cleared_record["settings"]["modes"]["default"] == {"model": "gpt-5.1", "effort": "high"}

    await bridge.handle_update(message_update("after clear", message_id=12))

    record = store.load_threads()[key]
    assert record["thread_id"] == "thr_2"
    assert record["settings"]["modes"]["default"] == {"model": "gpt-5.1", "effort": "high"}
    assert app_server.thread_starts[-1]["model"] == "gpt-5.1"
    assert app_server.thread_settings_updates[-1] == {"thread_id": "thr_2", "effort": "high", "model": "gpt-5.1"}


@pytest.mark.asyncio
async def test_reset_clears_saved_thread_preferences(tmp_path: Path) -> None:
    bridge, _bot, app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")

    await bridge.handle_update(message_update("/model gpt-5.1 high"))
    await bridge.handle_update(message_update("/reset", message_id=11))

    assert store.load_threads() == {}

    await bridge.handle_update(message_update("after reset", message_id=12))

    key = TelegramStateStore.thread_key(42, bridge.settings.default_cwd)
    assert store.load_threads()[key]["thread_id"] == "thr_2"
    assert "settings" not in store.load_threads()[key]
    assert app_server.thread_starts[-1]["model"] is None


@pytest.mark.asyncio
async def test_selection_callback_default_permission_sets_workspace_profile(tmp_path: Path) -> None:
    bridge, bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update("/permissions read-only"))
    await bridge.handle_update(message_update("/permissions", message_id=11))
    buttons = inline_button_map(bot.messages[-1])

    await bridge.handle_update(callback_update(buttons["Default"], message_id=bot.messages[-1]["message_id"]))

    assert app_server.thread_settings_updates[-1] == {
        "thread_id": "thr_1",
        "permissions": ":workspace",
        "approval_policy": "on-request",
    }
    assert "Permission profile set to :workspace; approval policy set to on-request" in bot.edits[-1]["text"]


@pytest.mark.asyncio
async def test_effort_without_arguments_points_to_model_flow(tmp_path: Path) -> None:
    bridge, bot, app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")

    await bridge.handle_update(message_update("/effort"))

    assert "Use /model to select a model and reasoning effort together." in bot.messages[-1]["text"]
    assert app_server.thread_settings_updates == []
    assert store.load_pending_selections() == {}


@pytest.mark.asyncio
async def test_selection_callback_rejects_unknown_expired_and_other_user(tmp_path: Path) -> None:
    now = datetime(2026, 5, 24, tzinfo=timezone.utc)
    bridge, bot, app_server, store, access = bridge_for(tmp_path, now=now)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update("/approval"))
    buttons = inline_button_map(bot.messages[-1])

    await bridge.handle_update(callback_update(buttons["Never"], user_id=999, message_id=bot.messages[-1]["message_id"]))
    assert app_server.thread_settings_updates == []
    assert "not allowed" in bot.answers[-1]["text"]

    await bridge.handle_update(callback_update("select:unknown-token", message_id=bot.messages[-1]["message_id"]))
    assert app_server.thread_settings_updates == []
    assert bot.answers[-1]["text"] == "Selection expired."

    token = selection_token(buttons["Never"])
    pending = store.load_pending_selections()
    pending[token]["expires_at"] = (now - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    store.save_pending_selections(pending)
    await bridge.handle_update(callback_update(buttons["Never"], message_id=bot.messages[-1]["message_id"]))

    assert app_server.thread_settings_updates == []
    assert "expired" in bot.edits[-1]["text"].lower()


@pytest.mark.asyncio
async def test_permissions_default_uses_workspace_profile_for_subsequent_turns(tmp_path: Path) -> None:
    bridge, _bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")

    await bridge.handle_update(message_update("/permissions read-only"))
    await bridge.handle_update(message_update("/permissions default", message_id=11))
    await bridge.handle_update(message_update("after default", message_id=12))

    assert app_server.thread_settings_updates[-1] == {
        "thread_id": "thr_1",
        "permissions": ":workspace",
        "approval_policy": "on-request",
    }
    assert app_server.turn_starts[-1]["permissions"] == ":workspace"
    assert "sandbox_policy" not in app_server.turn_starts[-1]


@pytest.mark.parametrize(
    ("typed_value", "expected_profile"),
    [
        ("Read Only", ":read-only"),
        ("read-only", ":read-only"),
        (":read-only", ":read-only"),
        ("default", ":workspace"),
        ("auto-review", ":auto-review"),
        ("full-access", ":danger-full-access"),
    ],
)
@pytest.mark.asyncio
async def test_typed_permission_command_resolves_real_app_server_profile_ids(
    tmp_path: Path,
    typed_value: str,
    expected_profile: str,
) -> None:
    bridge, bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")

    async def real_shape_permission_profile_list(**kwargs: Any) -> dict[str, Any]:
        app_server.permission_profile_lists.append(kwargs)
        return {
            "data": [
                {"id": ":read-only", "description": None},
                {"id": ":workspace", "description": None},
                {"id": ":auto-review", "description": None},
                {"id": ":danger-full-access", "description": None},
            ]
        }

    app_server.permission_profile_list = real_shape_permission_profile_list

    await bridge.handle_update(message_update(f"/permissions {typed_value}"))

    assert app_server.permission_profile_lists == [{"cwd": str(bridge.settings.default_cwd)}]
    expected_approval = "never" if expected_profile == ":danger-full-access" else "on-request"
    assert app_server.thread_settings_updates == [
        {"thread_id": "thr_1", "permissions": expected_profile, "approval_policy": expected_approval}
    ]
    assert (
        bot.messages[-1]["text"]
        == f"Permission profile set to {expected_profile}; approval policy set to {expected_approval} for subsequent turns."
    )


@pytest.mark.asyncio
async def test_cancel_and_steer_use_active_in_memory_turn(tmp_path: Path) -> None:
    bridge, bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")

    await bridge.handle_update(message_update("/cancel"))
    await bridge.handle_update(message_update("/steer keep going", message_id=11))
    assert [message["text"] for message in bot.messages] == [
        "No active turn to cancel.",
        "No active turn to steer.",
    ]

    await bridge.handle_update(message_update("start a turn", message_id=12))
    await bridge.handle_update(message_update("/steer add more detail", message_id=13))
    await bridge.handle_update(message_update("/interrupt", message_id=14))

    assert app_server.turn_steers == [
        {
            "thread_id": "thr_1",
            "expected_turn_id": "turn_1",
            "input_items": [{"type": "text", "text": "add more detail"}],
        }
    ]
    assert app_server.turn_interrupts == [{"thread_id": "thr_1", "turn_id": "turn_1"}]
    assert bot.messages[-2]["text"] == "Steer request sent."
    assert bot.messages[-1]["text"] == "Cancel requested."


@pytest.mark.asyncio
async def test_active_turn_disabled_command_does_not_thread_read_before_stale(tmp_path: Path) -> None:
    bridge, bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")

    await bridge.handle_update(message_update("start a long turn"))
    await bridge.handle_update(message_update("/model", message_id=11))

    assert app_server.thread_reads == []
    assert bot.messages[-1]["text"] == "/model is disabled while a task is in progress. Use /steer <text> or /cancel."


@pytest.mark.asyncio
async def test_no_progress_turn_sends_one_notice_and_runs_bounded_reconciliation(tmp_path: Path) -> None:
    bridge, bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update("start a long turn"))
    context = bridge._active_turn_context("42")
    assert context is not None
    context.last_progress_at = access.now_fn() - timedelta(seconds=301)

    assert await bridge._active_turn_context_or_recover("42") is context
    first_message_count = len(bot.messages)
    assert "not shown progress" in bot.messages[-1]["text"]
    assert app_server.thread_reads == [{"thread_id": "thr_1", "include_turns": True}]

    assert await bridge._active_turn_context_or_recover("42") is context
    assert len(bot.messages) == first_message_count
    assert app_server.thread_reads == [{"thread_id": "thr_1", "include_turns": True}]


@pytest.mark.asyncio
async def test_stale_completed_turn_recovery_notifies_once(tmp_path: Path) -> None:
    bridge, bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update("start a turn"))
    context = bridge._active_turn_context("42")
    assert context is not None
    context.last_progress_at = access.now_fn() - timedelta(seconds=301)

    async def completed_thread_read(**kwargs: Any) -> dict[str, Any]:
        app_server.thread_reads.append(kwargs)
        return {
            "thread": {
                "id": kwargs["thread_id"],
                "status": {"type": "idle"},
                "turns": [{"id": context.turn_id, "status": "completed"}],
            }
        }

    app_server.thread_read = completed_thread_read

    assert await bridge._active_turn_context_or_recover("42") is None
    assert context.completed is True
    assert [message["text"] for message in bot.messages] == [
        "This turn has not shown progress recently. Checking its status.",
        "Recovered the completed turn state. You can send the next message.",
    ]

    assert await bridge._active_turn_context_or_recover("42") is None
    assert len(bot.messages) == 2


@pytest.mark.asyncio
async def test_waiting_approval_is_status_not_stale_reconciled(tmp_path: Path) -> None:
    bridge, bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update("needs approval"))
    await bridge.handle_app_server_request(
        AppServerEvent(
            "item/commandExecution/requestApproval",
            {"turnId": "turn_1", "command": "pytest"},
            request_id=77,
        )
    )
    context = bridge._active_turn_context("42")
    assert context is not None
    context.last_progress_at = access.now_fn() - timedelta(seconds=901)

    assert await bridge._active_turn_context_or_recover("42") is context
    assert app_server.thread_reads == []

    await bridge.handle_update(message_update("/status", message_id=12))
    status_text = bot.messages[-1]["text"]
    assert "Turn state: waiting on approval" in status_text
    assert "Recovery: use /steer <text> or /cancel." in status_text


@pytest.mark.asyncio
async def test_update_handling_failure_sends_one_notice_and_dead_letters_update(tmp_path: Path) -> None:
    bridge, bot, _app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")

    async def fail_update(_update: dict[str, Any]) -> None:
        raise JsonRpcError("request timed out")

    bridge.handle_update = fail_update  # type: ignore[method-assign]

    update = message_update("hello", message_id=55)
    assert await handle_update_with_recovery(bridge, update) is False
    assert await handle_update_with_recovery(bridge, update) is False

    assert [message["text"] for message in bot.messages] == [
        "Codex did not respond in time. Please try again in a moment."
    ]
    assert store.load_chats()["chat_id:42"]["last_update_id"] == 55


@pytest.mark.asyncio
async def test_app_server_reconnecting_state_fails_dependent_updates_fast(tmp_path: Path) -> None:
    bridge, bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    bridge.app_server_state = "reconnecting"

    await bridge.handle_update(message_update("start work"))
    await bridge.handle_update(message_update("start work again", message_id=11))
    await bridge.handle_update(message_update("/status", message_id=12))

    assert app_server.turn_starts == []
    assert [message["text"] for message in bot.messages[:1]] == [
        "Codex is reconnecting. I'll keep this chat active."
    ]
    assert len([message for message in bot.messages if "Codex is reconnecting" in message["text"]]) == 1
    assert "App-server: reconnecting" in bot.messages[-1]["text"]


@pytest.mark.parametrize(
    ("command_text", "expected_fragment"),
    [
        ("/steer", "Use /steer <text>."),
        ("/approval sometimes", "Use /approval <untrusted|on-failure|on-request|never>."),
        ("/effort extreme", "Use /model to select a model and reasoning effort together."),
        ("/mode missing", "Collaboration mode not found: missing"),
        ("/goal set", "Use /goal set <text>."),
        ("/goal bogus", "Use /goal, /goal set <text>, or /goal clear."),
        ("/rename", "Use /rename <title>."),
        ("/mcp tools", "Use /mcp, /mcp verbose, or /mcp reload."),
    ],
)
@pytest.mark.asyncio
async def test_slash_commands_report_missing_or_invalid_arguments(
    tmp_path: Path,
    command_text: str,
    expected_fragment: str,
) -> None:
    bridge, bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")

    await bridge.handle_update(message_update(command_text))

    assert expected_fragment in bot.messages[-1]["text"]
    assert app_server.thread_settings_updates == []
    assert app_server.turn_starts == []


@pytest.mark.parametrize("command_name", sorted(UNSUPPORTED_COMMANDS))
@pytest.mark.asyncio
async def test_unsupported_terminal_commands_return_local_gateway_message(
    tmp_path: Path,
    command_name: str,
) -> None:
    bridge, bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")

    await bridge.handle_update(message_update(f"/{command_name}"))

    if command_name in {"usage", "context"}:
        assert "Use /status" in bot.messages[-1]["text"]
    elif command_name == "plugin":
        assert "Use /plugins" in bot.messages[-1]["text"]
    else:
        assert "is not available from Telegram Gateway" in bot.messages[-1]["text"]
    assert app_server.thread_starts == []
    assert app_server.turn_starts == []


@pytest.mark.parametrize("command_name", sorted(UNKNOWN_COMMAND_CONTRACT_EXAMPLES))
@pytest.mark.asyncio
async def test_unknown_slash_commands_return_local_gateway_message(
    tmp_path: Path,
    command_name: str,
) -> None:
    bridge, bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")

    await bridge.handle_update(message_update(f"/{command_name}"))

    assert bot.messages[-1]["text"] == f"Unknown command: /{command_name}"
    assert app_server.thread_starts == []
    assert app_server.turn_starts == []


@pytest.mark.parametrize(
    ("command_text", "call_attr", "expected_fragment"),
    [
        ("/features", "feature_lists", "Memories"),
        ("/account", "account_reads", "user@example.com"),
        ("/limits", "account_rate_limit_reads", "42%"),
        ("/hooks", "hook_lists", "lint"),
        ("/mcp", "mcp_status_lists", "github"),
        ("/apps", "app_lists", "GitHub"),
        ("/config", "config_reads", "gpt-5.1"),
        ("/skills", "skills", "skill-creator"),
    ],
)
@pytest.mark.asyncio
async def test_direct_app_server_list_and_read_commands_are_formatted(
    tmp_path: Path,
    command_text: str,
    call_attr: str,
    expected_fragment: str,
) -> None:
    bridge, bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")

    await bridge.handle_update(message_update(command_text))

    assert getattr(app_server, call_attr)
    assert expected_fragment in bot.messages[-1]["text"]
    assert app_server.turn_starts == []


@pytest.mark.parametrize(
    ("command_text", "empty_result", "expected_fragment"),
    [
        ("/features", {"data": []}, "No features found."),
        ("/apps", {"data": []}, "No apps found."),
    ],
)
@pytest.mark.asyncio
async def test_direct_app_server_list_commands_report_empty_results(
    tmp_path: Path,
    command_text: str,
    empty_result: dict[str, Any],
    expected_fragment: str,
) -> None:
    bridge, bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")

    async def empty_model_list(**kwargs: Any) -> dict[str, Any]:
        app_server.model_lists.append(kwargs)
        return empty_result

    async def empty_permission_profile_list(**kwargs: Any) -> dict[str, Any]:
        app_server.permission_profile_lists.append(kwargs)
        return empty_result

    async def empty_collaboration_mode_list() -> dict[str, Any]:
        app_server.mode_lists.append({})
        return empty_result

    async def empty_experimental_feature_list(**kwargs: Any) -> dict[str, Any]:
        app_server.feature_lists.append(kwargs)
        return empty_result

    async def empty_app_list(**kwargs: Any) -> dict[str, Any]:
        app_server.app_lists.append(kwargs)
        return empty_result

    app_server.model_list = empty_model_list
    app_server.permission_profile_list = empty_permission_profile_list
    app_server.collaboration_mode_list = empty_collaboration_mode_list
    app_server.experimental_feature_list = empty_experimental_feature_list
    app_server.app_list = empty_app_list

    await bridge.handle_update(message_update(command_text))

    assert expected_fragment in bot.messages[-1]["text"]


@pytest.mark.asyncio
async def test_apps_command_reports_feature_gated_forbidden_response(tmp_path: Path) -> None:
    bridge, bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")

    async def forbidden_apps(**_kwargs: Any) -> dict[str, Any]:
        raise JsonRpcError("failed to list apps: Request failed with status 403 Forbidden")

    app_server.app_list = forbidden_apps

    await bridge.handle_update(message_update("/apps"))

    assert bot.messages[-1]["text"] == "Apps are not available for this account/config."


@pytest.mark.asyncio
async def test_mcp_reload_uses_config_reload_method(tmp_path: Path) -> None:
    bridge, bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")

    await bridge.handle_update(message_update("/mcp reload"))

    assert app_server.mcp_reloads == [{}]
    assert bot.messages[-1]["text"] == "MCP server configuration reloaded."


@pytest.mark.asyncio
async def test_threads_uses_app_server_list_and_local_fallback(tmp_path: Path) -> None:
    bridge, bot, app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    app_server.thread_list_result = {
        "data": [
            {
                "id": "thr_remote",
                "title": "Remote gateway work",
                "cwd": str(bridge.settings.default_cwd),
                "updatedAt": "2026-05-24T00:00:00Z",
            }
        ]
    }

    await bridge.handle_update(message_update("/threads gateway"))

    assert app_server.thread_lists == [
        {"cwd": str(bridge.settings.default_cwd), "search_term": "gateway", "limit": 10}
    ]
    assert "Remote gateway work" in bot.messages[-1]["text"]

    app_server.thread_list_error = JsonRpcError("thread list unavailable")
    store.save_threads(
        {
            TelegramStateStore.thread_key(42, bridge.settings.default_cwd): {
                "thread_id": "thr_local",
                "workspace": str(bridge.settings.default_cwd),
                "created_at": "2026-05-24T00:00:00Z",
                "updated_at": "2026-05-24T00:00:00Z",
            }
        }
    )

    await bridge.handle_update(message_update("/threads", message_id=11))

    assert "thr_local" in bot.messages[-1]["text"]


@pytest.mark.asyncio
async def test_threads_and_resume_use_thread_read_snippet_when_title_is_missing(tmp_path: Path) -> None:
    bridge, bot, app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    app_server.thread_list_result = {"data": [{"id": "thr_remote", "cwd": str(bridge.settings.default_cwd)}]}
    prompt = (
        "A previous agent produced the plan below to accomplish the user's task. "
        "Implement the plan in a fresh context."
    )

    async def read_thread(**kwargs: Any) -> dict[str, Any]:
        app_server.thread_reads.append(kwargs)
        return {"thread": {"id": kwargs["thread_id"], "turns": [{"items": [{"type": "userMessage", "text": prompt}]}]}}

    app_server.thread_read = read_thread

    await bridge.handle_update(message_update("/threads", message_id=11))

    assert "A previous agent produced the plan below" in bot.messages[-1]["text"]
    assert app_server.thread_reads[-1] == {"thread_id": "thr_remote", "include_turns": True}

    await bridge.handle_update(message_update("/resume", message_id=12))
    buttons = inline_button_map(bot.messages[-1])
    label = next(text for text in buttons if text.startswith("A previous agent produced"))
    await bridge.handle_update(callback_update(buttons[label], message_id=bot.messages[-1]["message_id"]))

    record = store.load_threads()[TelegramStateStore.thread_key("42", bridge.settings.default_cwd)]
    assert record["thread_id"] == "thr_remote"
    assert record["title"].startswith("A previous agent produced")


@pytest.mark.asyncio
async def test_exec_command_is_rejected_when_not_enabled(tmp_path: Path) -> None:
    bridge, bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")

    await bridge.handle_update(message_update("/exec python -V"))

    assert "disabled" in bot.messages[-1]["text"].lower()
    assert app_server.thread_starts == []
    assert app_server.turn_starts == []


@pytest.mark.asyncio
async def test_exec_command_starts_turn_when_enabled(tmp_path: Path) -> None:
    bridge, _bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    object.__setattr__(bridge.settings, "enable_exec", True)

    await bridge.handle_update(message_update("/exec python -V"))

    assert app_server.turn_starts[0]["input_items"] == [
        {"type": "text", "text": "Run this shell command in the active workspace: python -V"}
    ]


@pytest.mark.asyncio
async def test_workspace_set_changes_active_workspace_and_thread_mapping(tmp_path: Path) -> None:
    bridge, _bot, app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    repo_b = bridge.settings.allowed_roots[0] / "repo-b"
    repo_b.mkdir()

    await bridge.handle_update(message_update("/workspace set repo-b"))
    await bridge.handle_update(message_update("work here", message_id=11))

    chat_state = store.load_chats()[TelegramStateStore.chat_key(42)]
    assert chat_state["active_workspace"] == str(repo_b.resolve(strict=False))
    assert app_server.thread_starts[0]["cwd"] == str(repo_b.resolve(strict=False))
    assert TelegramStateStore.thread_key(42, repo_b) in store.load_threads()


@pytest.mark.asyncio
async def test_setcwd_alias_changes_active_workspace(tmp_path: Path) -> None:
    bridge, bot, _app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    repo_b = bridge.settings.allowed_roots[0] / "repo-b"
    repo_b.mkdir()

    await bridge.handle_update(message_update("/setcwd repo-b"))

    chat_state = store.load_chats()[TelegramStateStore.chat_key(42)]
    assert chat_state["active_workspace"] == str(repo_b.resolve(strict=False))
    assert bot.messages[-1]["text"] == f"Workspace set: {repo_b.resolve(strict=False)}"


@pytest.mark.asyncio
async def test_new_command_starts_fresh_thread_for_active_workspace(tmp_path: Path) -> None:
    bridge, bot, app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update("first turn"))
    await complete_active_turn(bridge)

    await bridge.handle_update(message_update("/new", message_id=11))

    key = TelegramStateStore.thread_key(42, bridge.settings.default_cwd)
    assert app_server.thread_starts[-1]["cwd"] == str(bridge.settings.default_cwd)
    assert store.load_threads()[key]["thread_id"] == "thr_2"
    assert bot.messages[-1]["text"] == f"Started a new Codex thread for {bridge.settings.default_cwd}."


@pytest.mark.asyncio
async def test_workspace_persists_across_start_new_and_clear_until_reset(tmp_path: Path) -> None:
    bridge, _bot, app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    repo_b = bridge.settings.allowed_roots[0] / "repo-b"
    repo_b.mkdir()

    await bridge.handle_update(message_update("/setcwd repo-b"))
    await bridge.handle_update(message_update("/start", message_id=11))
    await bridge.handle_update(message_update("/new", message_id=12))

    assert store.load_chats()[TelegramStateStore.chat_key(42)]["active_workspace"] == str(repo_b.resolve(strict=False))
    assert app_server.thread_starts[-1]["cwd"] == str(repo_b.resolve(strict=False))

    await bridge.handle_update(message_update("/reset", message_id=13))
    await bridge.handle_update(message_update("/new", message_id=14))

    assert app_server.thread_starts[-1]["cwd"] == str(bridge.settings.default_cwd)

    await bridge.handle_update(message_update("/setcwd repo-b", message_id=15))
    await bridge.handle_update(message_update("/clear", message_id=16))
    await bridge.handle_update(message_update("/new", message_id=17))

    assert store.load_chats()[TelegramStateStore.chat_key(42)]["active_workspace"] == str(repo_b.resolve(strict=False))
    assert app_server.thread_starts[-1]["cwd"] == str(repo_b.resolve(strict=False))


@pytest.mark.asyncio
async def test_completed_first_turn_auto_names_new_thread_from_user_prompt(tmp_path: Path) -> None:
    bridge, _bot, app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    prompt = (
        "A previous agent produced the plan below to accomplish the user's task. "
        "Implement the plan in a fresh context with focused tests."
    )

    await bridge.handle_update(message_update(prompt))
    await bridge.handle_app_event(
        AppServerEvent(
            "turn/completed",
            {"threadId": "thr_1", "turn": {"id": "turn_1", "status": "completed"}},
        )
    )
    await drain_background_tasks(bridge)

    assert app_server.thread_names[0]["thread_id"] == "thr_1"
    assert app_server.thread_names[0]["name"].startswith("A previous agent produced the plan below")
    assert app_server.thread_names[0]["name"].endswith("...")
    assert len(app_server.thread_names[0]["name"]) <= 96
    record = store.load_threads()[TelegramStateStore.thread_key("42", bridge.settings.default_cwd)]
    assert record["title"] == app_server.thread_names[0]["name"]
    assert record["auto_name_pending"] is False
    assert record["auto_generated_title"] is True


@pytest.mark.asyncio
async def test_completed_turn_reply_does_not_wait_for_auto_name_request(tmp_path: Path) -> None:
    bridge, bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    name_started = asyncio.Event()
    release_name = asyncio.Event()

    async def blocking_thread_set_name(**kwargs: Any) -> dict[str, Any]:
        app_server.thread_names.append(kwargs)
        name_started.set()
        await release_name.wait()
        return {}

    app_server.thread_set_name = blocking_thread_set_name  # type: ignore[method-assign]

    await bridge.handle_update(message_update("Name this thread"))
    context = bridge._active_turn_context("42")
    assert context is not None
    await bridge.handle_app_event(AppServerEvent("item/agentMessage/delta", {"turnId": context.turn_id, "delta": "done"}))

    await asyncio.wait_for(
        bridge.handle_app_event(
            AppServerEvent(
                "turn/completed",
                {"threadId": context.thread_id, "turn": {"id": context.turn_id, "status": "completed"}},
            )
        ),
        timeout=1,
    )

    assert bot.messages[-1]["text"] == "done"
    await asyncio.wait_for(name_started.wait(), timeout=1)
    release_name.set()
    await drain_background_tasks(bridge)
    assert app_server.thread_names[0]["thread_id"] == "thr_1"


@pytest.mark.asyncio
async def test_resume_lists_current_thread_with_inline_button(tmp_path: Path) -> None:
    bridge, bot, _app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    store.save_threads(
        {
            TelegramStateStore.thread_key(42, bridge.settings.default_cwd): {
                "thread_id": "thr_existing",
                "workspace": str(bridge.settings.default_cwd),
                "created_at": "2026-05-24T00:00:00Z",
                "updated_at": "2026-05-24T00:00:00Z",
            }
        }
    )

    await bridge.handle_update(message_update("/resume"))

    assert "Select a thread" in bot.messages[-1]["text"]
    assert bot.messages[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "resume:thr_existing"


@pytest.mark.asyncio
async def test_resume_callback_selects_thread_mapping(tmp_path: Path) -> None:
    bridge, bot, _app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    store.save_threads(
        {
            TelegramStateStore.thread_key(42, bridge.settings.default_cwd): {
                "thread_id": "thr_existing",
                "workspace": str(bridge.settings.default_cwd),
                "created_at": "2026-05-24T00:00:00Z",
                "updated_at": "2026-05-24T00:00:00Z",
            }
        }
    )

    await bridge.handle_update(callback_update("resume:thr_existing"))

    assert "Resumed" in bot.edits[-1]["text"]
    assert_callback_keyboard_cleared(bot.edits[-1])
    assert bot.answers[-1]["text"] == "Thread selected."


@pytest.mark.asyncio
async def test_tampered_active_workspace_outside_allowed_roots_is_reset(tmp_path: Path) -> None:
    bridge, bot, app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    store.save_chats(
        {
            TelegramStateStore.chat_key(42): {
                "active_workspace": str(tmp_path / "outside"),
                "allowed_user_ids": ["123"],
            }
        }
    )

    await bridge.handle_update(message_update("stay inside"))

    assert app_server.thread_starts[0]["cwd"] == str(bridge.settings.default_cwd)
    assert "outside allowed roots" in bot.messages[0]["text"]


@pytest.mark.asyncio
async def test_document_download_is_stored_safely_and_sent_to_codex(tmp_path: Path) -> None:
    bridge, bot, app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    bot.files["file_1"] = {"file_path": "docs/note.txt", "file_size": 4}
    bot.downloads["docs/note.txt"] = b"note"
    update = message_update("summarize", message_id=12)
    update["message"]["document"] = {
        "file_id": "file_1",
        "file_name": "../secret.txt",
        "file_size": 4,
        "mime_type": "text/plain",
    }

    await bridge.handle_update(update)

    saved = store.downloads_dir(42, 12) / "secret.txt"
    assert saved.read_bytes() == b"note"
    assert str(saved) in app_server.turn_starts[0]["input_items"][1]["text"]


@pytest.mark.asyncio
async def test_image_document_download_is_sent_to_codex_as_local_image(tmp_path: Path) -> None:
    bridge, bot, app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    bot.files["image_1"] = {"file_path": "docs/hair.png", "file_size": 7}
    bot.downloads["docs/hair.png"] = b"pngdata"
    update = message_update("make the hair red", message_id=14)
    update["message"]["document"] = {
        "file_id": "image_1",
        "file_name": "hair.png",
        "file_size": 7,
        "mime_type": "image/png",
    }

    await bridge.handle_update(update)

    saved = store.downloads_dir(42, 14) / "hair.png"
    assert saved.read_bytes() == b"pngdata"
    assert app_server.turn_starts[0]["input_items"][0] == {"type": "text", "text": "make the hair red"}
    assert app_server.turn_starts[0]["input_items"][1] == {
        "type": "localImage",
        "path": str(saved),
        "detail": "original",
    }
    assert "MIME type: image/png" in app_server.turn_starts[0]["input_items"][2]["text"]


@pytest.mark.asyncio
async def test_photo_attachment_download_uses_largest_variant_and_caption_text(tmp_path: Path) -> None:
    bridge, bot, app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    bot.files["photo_big"] = {"file_path": "photos/big.png", "file_size": 4}
    bot.downloads["photos/big.png"] = b"png"
    update = message_update("", message_id=13)
    update["message"].pop("text")
    update["message"]["caption"] = "make the hair red"
    update["message"]["photo"] = [
        {"file_id": "photo_small", "file_unique_id": "small", "file_size": 1},
        {"file_id": "photo_big", "file_unique_id": "big", "file_size": 4},
    ]

    await bridge.handle_update(update)

    saved = store.downloads_dir(42, 13) / "photo_big.png"
    assert saved.read_bytes() == b"png"
    assert app_server.turn_starts[0]["input_items"][0] == {"type": "text", "text": "make the hair red"}
    assert app_server.turn_starts[0]["input_items"][1] == {
        "type": "localImage",
        "path": str(saved),
        "detail": "original",
    }
    assert "photo_big.png" in app_server.turn_starts[0]["input_items"][2]["text"]
    assert "MIME type: image/png" in app_server.turn_starts[0]["input_items"][2]["text"]


@pytest.mark.parametrize(
    ("payload", "file_id", "file_path", "data", "expected_type", "expected_text"),
    [
        (
            {"video": {"file_id": "video_1", "file_unique_id": "v1", "file_size": 9, "duration": 5, "width": 640, "height": 360, "mime_type": "video/mp4"}},
            "video_1",
            "videos/clip.mp4",
            b"mp4 bytes",
            "video",
            "Duration: 5",
        ),
        (
            {
                "animation": {"file_id": "anim_1", "file_unique_id": "a1", "file_size": 7, "duration": 2, "width": 320, "height": 180, "mime_type": "image/gif"},
                "document": {"file_id": "doc_duplicate", "file_name": "duplicate.gif", "file_size": 7},
            },
            "anim_1",
            "animations/loop.gif",
            b"gifdata",
            "animation",
            "Payload type: animation",
        ),
        (
            {"audio": {"file_id": "audio_1", "file_unique_id": "au1", "file_name": "song.mp3", "file_size": 5, "mime_type": "audio/mpeg", "duration": 30, "performer": "Ada", "title": "Theme"}},
            "audio_1",
            "audio/song.mp3",
            b"audio",
            "audio",
            "Performer: Ada",
        ),
        (
            {"voice": {"file_id": "voice_1", "file_unique_id": "vo1", "file_size": 5, "mime_type": "audio/ogg", "duration": 8}},
            "voice_1",
            "voice/note.oga",
            b"voice",
            "voice",
            "Duration: 8",
        ),
        (
            {"video_note": {"file_id": "vn_1", "file_unique_id": "vn1", "file_size": 5, "duration": 4, "length": 240}},
            "vn_1",
            "video_notes/note.mp4",
            b"note",
            "video_note",
            "Length: 240",
        ),
        (
            {"sticker": {"file_id": "sticker_1", "file_unique_id": "s1", "file_size": 5, "emoji": ":)", "type": "regular", "is_animated": False, "is_video": False}},
            "sticker_1",
            "stickers/smile.webp",
            b"webp",
            "sticker",
            "Emoji: :)",
        ),
        (
            {
                "live_photo": {"file_id": "live_1", "file_unique_id": "lp1", "file_size": 5, "duration": 3, "width": 720, "height": 1280},
                "photo": [{"file_id": "photo_duplicate", "file_unique_id": "pd", "file_size": 3}],
            },
            "live_1",
            "live/live.mp4",
            b"live",
            "live_photo",
            "Payload type: live_photo",
        ),
        (
            {
                "paid_media": {
                    "star_count": 25,
                    "paid_media": [
                        {"type": "preview", "width": 100, "height": 100},
                        {"type": "video", "video": {"file_id": "paid_video_1", "file_unique_id": "pv1", "file_size": 6, "duration": 6, "mime_type": "video/mp4"}},
                    ],
                }
            },
            "paid_video_1",
            "paid/video.mp4",
            b"paid",
            "paid_media.video",
            "Paid media stars: 25",
        ),
    ],
)
@pytest.mark.asyncio
async def test_native_downloadable_payloads_are_stored_with_metadata(
    tmp_path: Path,
    payload: dict[str, Any],
    file_id: str,
    file_path: str,
    data: bytes,
    expected_type: str,
    expected_text: str,
) -> None:
    bridge, bot, app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    bot.files[file_id] = {"file_path": file_path, "file_size": len(data)}
    bot.downloads[file_path] = data
    update = message_update("inspect", message_id=15)
    update["message"].update(payload)

    await bridge.handle_update(update)

    saved_files = list(store.downloads_dir(42, 15).iterdir())
    assert len(saved_files) == 1
    assert saved_files[0].read_bytes() == data
    context = bridge._active_turn_context("42")
    assert context is not None
    assert context.attachments[file_id]["telegram_payload_type"] == expected_type
    attachment_text = "\n".join(
        item["text"] for item in app_server.turn_starts[0]["input_items"] if item["type"] == "text"
    )
    assert expected_text in attachment_text


@pytest.mark.parametrize(
    ("payload", "expected", "unexpected"),
    [
        ({"contact": {"phone_number": "+15551212", "first_name": "Ada", "last_name": "Lovelace", "user_id": 99}}, "Telegram contact", None),
        ({"location": {"latitude": 14.6, "longitude": 121.0, "horizontal_accuracy": 10}}, "Telegram location", None),
        (
            {
                "venue": {
                    "location": {"latitude": 14.6, "longitude": 121.0},
                    "title": "HQ",
                    "address": "Main St",
                },
                "location": {"latitude": 1, "longitude": 2},
            },
            "Telegram venue",
            "Telegram location",
        ),
        ({"poll": {"id": "poll_1", "question": "Ship?", "options": [{"text": "Yes", "voter_count": 2}], "total_voter_count": 2}}, "Telegram poll", None),
        ({"dice": {"emoji": "🎲", "value": 4}}, "Telegram dice", None),
        ({"checklist": {"title": "Launch", "tasks": [{"id": 1, "text": "Test"}, {"id": 2, "text": "Ship", "completion_date": 1}]}}, "Telegram checklist", None),
        ({"story": {"chat": {"id": -100}, "id": 7}}, "Telegram story", None),
        ({"game": {"title": "Puzzle", "description": "Solve it"}}, "Telegram game", None),
        ({"invoice": {"title": "Plan", "currency": "XTR", "total_amount": 500}}, "Telegram invoice", None),
        ({"successful_payment": {"currency": "XTR", "total_amount": 500, "invoice_payload": "payload"}}, "Telegram successful payment", None),
        ({"gift": {"id": "gift_1"}}, "Telegram gift", None),
        ({"users_shared": {"request_id": 3, "users": [{"user_id": 123}]}}, "Telegram users shared", None),
        ({"chat_shared": {"request_id": 4, "chat_id": -100}}, "Telegram chat shared", None),
        ({"web_app_data": {"button_text": "Open", "data": "{\"ok\":true}"}}, "Telegram web app data", None),
        ({"new_chat_title": "New title"}, "Telegram service: new chat title", None),
    ],
)
@pytest.mark.asyncio
async def test_structured_payloads_are_summarized_for_codex(
    tmp_path: Path,
    payload: dict[str, Any],
    expected: str,
    unexpected: str | None,
) -> None:
    bridge, _bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    update = message_update("", message_id=16)
    update["message"].update(payload)

    await bridge.handle_update(update)

    text = app_server.turn_starts[0]["input_items"][0]["text"]
    assert expected in text
    if unexpected:
        assert unexpected not in text


@pytest.mark.asyncio
async def test_inbound_poll_summary_includes_vote_counts(tmp_path: Path) -> None:
    bridge, _bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    update = message_update("", message_id=17)
    update["message"]["poll"] = {
        "id": "poll_1",
        "question": "Ship?",
        "options": [
            {"text": "Yes", "voter_count": 2},
            {"text": "No", "voter_count": 1},
        ],
        "total_voter_count": 3,
    }

    await bridge.handle_update(update)

    text = app_server.turn_starts[0]["input_items"][0]["text"]
    assert "total_voter_count: 3" in text
    assert "option_1: Yes (2 votes)" in text
    assert "option_2: No (1 votes)" in text


@pytest.mark.asyncio
async def test_oversized_attachment_is_rejected_before_download(tmp_path: Path) -> None:
    bridge, bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    object.__setattr__(bridge.settings, "max_attachment_bytes", 3)
    update = message_update("summarize", message_id=12)
    update["message"]["document"] = {"file_id": "file_1", "file_name": "big.txt", "file_size": 4}

    await bridge.handle_update(update)

    assert "too large" in bot.messages[-1]["text"]
    assert app_server.turn_starts == []


@pytest.mark.asyncio
async def test_image_generation_output_is_sent_to_telegram_photo(tmp_path: Path) -> None:
    bridge, bot, _app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update("make an image"))
    output = bridge.settings.default_cwd / "red-hair.png"
    output.write_bytes(b"edited-png")
    item = {
        "id": "image_1",
        "type": "imageGeneration",
        "status": "completed",
        "result": "done",
        "savedPath": str(output),
    }

    await bridge.handle_app_event(
        AppServerEvent("item/completed", {"threadId": "thr_1", "turnId": "turn_1", "item": item})
    )
    await bridge.handle_app_event(
        AppServerEvent(
            "turn/completed",
            {"threadId": "thr_1", "turn": {"id": "turn_1", "status": "completed", "items": [item]}},
        )
    )

    assert bot.documents == []
    assert len(bot.photos) == 1
    assert bot.photos[0]["filename"] == "red-hair.png"
    assert bot.photos[0]["photo"] == b"edited-png"
    assert bot.photos[0]["caption"] == "Generated image"
    assert bot.photos[0]["content_type"] == "image/png"


@pytest.mark.asyncio
async def test_raw_image_generation_result_is_sent_to_telegram_photo(tmp_path: Path) -> None:
    bridge, bot, _app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update("edit this image"))
    image_bytes = b"\x89PNG\r\n\x1a\nraw"
    item = {
        "id": "ig_1",
        "type": "image_generation_call",
        "status": "generating",
        "result": "iVBORw0KGgpyYXc=",
    }

    await bridge.handle_app_event(
        AppServerEvent("item/completed", {"threadId": "thr_1", "turnId": "turn_1", "item": item})
    )
    await bridge.handle_app_event(
        AppServerEvent(
            "turn/completed",
            {"threadId": "thr_1", "turn": {"id": "turn_1", "status": "completed", "items": [item]}},
        )
    )

    assert bot.documents == []
    assert len(bot.photos) == 1
    assert bot.photos[0]["filename"].startswith("generated-image-")
    assert bot.photos[0]["photo"] == image_bytes
    assert bot.photos[0]["caption"] == "Generated image"
    assert bot.photos[0]["content_type"] == "image/png"


@pytest.mark.asyncio
async def test_image_view_output_is_not_echoed_to_telegram(tmp_path: Path) -> None:
    bridge, bot, _app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update("edit this image"))
    original = bridge.settings.default_cwd / "original.png"
    original.write_bytes(b"original-png")
    item = {
        "id": "image_view_1",
        "type": "imageView",
        "path": str(original),
    }

    await bridge.handle_app_event(
        AppServerEvent("item/completed", {"threadId": "thr_1", "turnId": "turn_1", "item": item})
    )
    await bridge.handle_app_event(
        AppServerEvent(
            "turn/completed",
            {"threadId": "thr_1", "turn": {"id": "turn_1", "status": "completed", "items": [item]}},
        )
    )

    assert bot.documents == []


@pytest.mark.asyncio
async def test_event_rendering_uses_documented_agent_message_events(tmp_path: Path) -> None:
    bridge, bot, _app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update("hi"))

    await bridge.handle_app_event(
        AppServerEvent(
            "item/agentMessage/delta",
            {"threadId": "thr_1", "turnId": "turn_1", "itemId": "item_1", "delta": "O"},
        )
    )
    await bridge.handle_app_event(
        AppServerEvent(
            "item/agentMessage/delta",
            {"threadId": "thr_1", "turnId": "turn_1", "itemId": "item_1", "delta": "K"},
        )
    )
    await bridge.handle_app_event(
        AppServerEvent(
            "item/completed",
            {"threadId": "thr_1", "turnId": "turn_1", "item": {"id": "item_1", "type": "agentMessage", "text": "OK"}},
        )
    )
    await bridge.handle_app_event(
        AppServerEvent(
            "turn/completed",
            {"threadId": "thr_1", "turn": {"id": "turn_1", "status": "completed", "items": []}},
        )
    )
    await bridge.handle_app_event(
        AppServerEvent(
            "turn/completed",
            {"threadId": "thr_1", "turn": {"id": "turn_1", "status": "completed", "items": []}},
        )
    )

    assert [message["text"] for message in bot.messages] == ["OK"]


@pytest.mark.asyncio
async def test_plan_updated_event_is_rendered_and_updated_in_telegram(tmp_path: Path) -> None:
    bridge, bot, _app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update("/plan inspect"))
    context = bridge._active_turn_context("42")
    assert context is not None

    await bridge.handle_app_event(
        AppServerEvent(
            "turn/plan/updated",
            {
                "threadId": context.thread_id,
                "turnId": context.turn_id,
                "explanation": "Use focused steps.",
                "plan": [
                    {"status": "completed", "step": "Inspect the /plan event path"},
                    {"status": "inProgress", "step": "Render plan updates in Telegram"},
                    {"status": "pending", "step": "Run focused tests"},
                ],
            },
        )
    )

    first_plan_message = bot.messages[-1]
    assert first_plan_message["text"] == (
        "Plan:\n"
        "Use focused steps.\n"
        "- [x] Inspect the /plan event path\n"
        "- [~] Render plan updates in Telegram\n"
        "- [ ] Run focused tests"
    )

    await bridge.handle_app_event(
        AppServerEvent(
            "turn/plan/updated",
            {
                "threadId": context.thread_id,
                "turnId": context.turn_id,
                "explanation": "Use focused steps.",
                "plan": [
                    {"status": "completed", "step": "Inspect the /plan event path"},
                    {"status": "completed", "step": "Render plan updates in Telegram"},
                    {"status": "inProgress", "step": "Run focused tests"},
                ],
            },
        )
    )

    assert len([message for message in bot.messages if message["text"].startswith("Plan:")]) == 1
    assert bot.edits[-1]["message_id"] == first_plan_message["message_id"]
    assert bot.edits[-1]["text"] == (
        "Plan:\n"
        "Use focused steps.\n"
        "- [x] Inspect the /plan event path\n"
        "- [x] Render plan updates in Telegram\n"
        "- [~] Run focused tests"
    )


@pytest.mark.asyncio
async def test_completed_plan_item_is_rendered_to_telegram(tmp_path: Path) -> None:
    bridge, bot, _app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update("make a plan"))
    context = bridge._active_turn_context("42")
    assert context is not None

    await bridge.handle_app_event(
        AppServerEvent(
            "item/completed",
            {
                "threadId": context.thread_id,
                "turnId": context.turn_id,
                "item": {"id": "plan_1", "type": "plan", "text": "1. Inspect\n2. Verify"},
            },
        )
    )

    assert bot.messages[-1]["text"] == "Plan:\n1. Inspect\n2. Verify"


@pytest.mark.asyncio
async def test_completed_plan_prompts_for_cli_style_implementation_choice(tmp_path: Path) -> None:
    bridge, bot, app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update("/plan inspect"))
    context = bridge._active_turn_context("42")
    assert context is not None
    await bridge.handle_app_event(
        AppServerEvent(
            "turn/plan/updated",
            {
                "threadId": context.thread_id,
                "turnId": context.turn_id,
                "plan": [{"status": "pending", "step": "Render plan choices in Telegram"}],
            },
        )
    )

    await complete_active_turn(bridge)

    assert bot.messages[-1]["text"] == PLAN_CHOICE_TEXT
    buttons = inline_button_map(bot.messages[-1])
    assert list(buttons) == [
        "Yes, implement this plan",
        "Yes, clear context and implement",
        "No, stay in Plan mode",
    ]

    pending = store.load_pending_selections()
    assert {record["action"] for record in pending.values()} == {
        "plan_implement",
        "plan_fresh",
        "plan_stay",
    }

    await bridge.handle_update(
        callback_update(buttons["Yes, implement this plan"], message_id=bot.messages[-1]["message_id"])
    )

    assert_callback_keyboard_cleared(bot.edits[-1])
    assert bot.edits[-1]["text"] == "Implementing this plan in Default mode."
    assert store.load_pending_selections() == {}
    assert app_server.thread_settings_updates[-1]["collaboration_mode"]["mode"] == "default"
    assert len(app_server.turn_starts) == 2
    assert app_server.turn_starts[-1]["thread_id"] == "thr_1"
    prompt = app_server.turn_starts[-1]["input_items"][0]["text"]
    assert prompt.startswith("Implement this plan now.")
    assert "Render plan choices in Telegram" in prompt
    await complete_active_turn(bridge)
    await drain_background_tasks(bridge)


@pytest.mark.asyncio
async def test_plan_turn_suppresses_buffered_progress_message_after_plan(tmp_path: Path) -> None:
    bridge, bot, _app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update("/plan inspect"))
    context = bridge._active_turn_context("42")
    assert context is not None
    await bridge.handle_app_event(
        AppServerEvent(
            "turn/plan/updated",
            {
                "threadId": context.thread_id,
                "turnId": context.turn_id,
                "plan": [{"status": "pending", "step": "Inspect the existing page"}],
            },
        )
    )
    await bridge.handle_app_event(
        AppServerEvent(
            "item/completed",
            {
                "threadId": context.thread_id,
                "turnId": context.turn_id,
                "item": {"id": "item_1", "type": "agentMessage", "text": "I am reading index.html before planning."},
            },
        )
    )

    await complete_active_turn(bridge)

    assert bot.messages[-1]["text"] == PLAN_CHOICE_TEXT
    assert "Inspect the existing page" in bot.messages[-2]["text"]
    assert all("reading index.html" not in message["text"] for message in bot.messages)


@pytest.mark.asyncio
async def test_clear_context_plan_choice_starts_fresh_default_thread(tmp_path: Path) -> None:
    bridge, bot, app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update("/plan inspect"))
    context = bridge._active_turn_context("42")
    assert context is not None
    await bridge.handle_app_event(
        AppServerEvent(
            "turn/plan/updated",
            {
                "threadId": context.thread_id,
                "turnId": context.turn_id,
                "plan": [{"status": "pending", "step": "Create the static page"}],
            },
        )
    )
    await complete_active_turn(bridge)
    buttons = inline_button_map(bot.messages[-1])

    await bridge.handle_update(
        callback_update(buttons["Yes, clear context and implement"], message_id=bot.messages[-1]["message_id"])
    )

    assert bot.edits[-1]["text"] == "Starting a fresh Default-mode thread to implement this plan."
    assert len(app_server.thread_starts) == 2
    assert app_server.turn_starts[-1]["thread_id"] == "thr_2"
    prompt = app_server.turn_starts[-1]["input_items"][0]["text"]
    assert prompt.startswith("A previous agent produced the plan below")
    assert "Create the static page" in prompt
    record = store.load_threads()[TelegramStateStore.thread_key(42, bridge.settings.default_cwd)]
    assert record["thread_id"] == "thr_2"
    assert record["settings"]["active_mode"] == "default"
    await complete_active_turn(bridge)
    await drain_background_tasks(bridge)


@pytest.mark.asyncio
async def test_status_command_reports_codex_status_and_latest_thread_token_usage(tmp_path: Path) -> None:
    bridge, bot, _app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update("hi"))

    await bridge.handle_app_event(
        AppServerEvent(
            "thread/tokenUsage/updated",
            {
                "threadId": "thr_1",
                "turnId": "turn_1",
                "tokenUsage": {
                    "modelContextWindow": 200000,
                    "last": {
                        "inputTokens": 1000,
                        "cachedInputTokens": 250,
                        "outputTokens": 400,
                        "reasoningOutputTokens": 50,
                        "totalTokens": 1400,
                    },
                    "total": {
                        "inputTokens": 3000,
                        "cachedInputTokens": 500,
                        "outputTokens": 900,
                        "reasoningOutputTokens": 100,
                        "totalTokens": 3900,
                    },
                },
            },
        )
    )
    await bridge.handle_update(message_update("/status", message_id=11))

    status_text = bot.messages[-1]["text"]
    assert "Codex status" in status_text
    assert "Model: gpt-5.1" in status_text
    assert f"Directory: {bridge.settings.default_cwd}" in status_text
    assert "Account: user@example.com (Pro)" in status_text
    assert "Session: thr_1" in status_text
    assert "Context window: 98.0% left (3,900 used / 200,000)" in status_text
    assert "Token usage: input 3,000, cached 500, output 900, reasoning 100" in status_text
    assert "5h limit: 58% left" in status_text
    key = TelegramStateStore.thread_key(42, bridge.settings.default_cwd)
    assert store.load_threads()[key]["token_usage_turn_id"] == "turn_1"


@pytest.mark.asyncio
async def test_status_command_reports_empty_context_state_before_usage_notification(tmp_path: Path) -> None:
    bridge, bot, _app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")

    await bridge.handle_update(message_update("/status"))

    assert "Session: none" in bot.messages[-1]["text"]
    assert "Context window: no active thread yet" in bot.messages[-1]["text"]


@pytest.mark.asyncio
async def test_event_rendering_suppresses_tool_chatter_but_reports_failures(tmp_path: Path) -> None:
    bridge, bot, _app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update("hi"))

    await bridge.handle_app_event(
        AppServerEvent(
            "item/started",
            {"threadId": "thr_1", "turnId": "turn_1", "item": {"type": "commandExecution", "command": "pytest"}},
        )
    )
    await bridge.handle_app_event(
        AppServerEvent(
            "item/completed",
            {"threadId": "thr_1", "turnId": "turn_1", "item": {"type": "fileChange", "changes": [{"path": "README.md"}]}},
        )
    )
    await bridge.handle_app_event(
        AppServerEvent(
            "turn/completed",
            {"threadId": "thr_1", "turn": {"id": "turn_1", "status": "failed", "error": {"message": "Bearer secret"}}},
        )
    )

    assert [message["text"] for message in bot.messages] == ["Turn failed: Bearer <redacted>"]


@pytest.mark.asyncio
async def test_approval_prompt_includes_reason_permissions_and_grant_root(tmp_path: Path) -> None:
    bridge, bot, _app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update("run tests"))

    await bridge.handle_app_server_request(
        AppServerEvent(
            "item/fileChange/requestApproval",
            {
                "threadId": "thr_1",
                "turnId": "turn_1",
                "reason": "Needs write access",
                "grantRoot": str(bridge.settings.default_cwd),
                "additionalPermissions": {"network": {"enabled": True}},
            },
            request_id=70,
        )
    )

    text = bot.messages[-1]["text"]
    assert "Needs write access" in text
    assert "grant root" in text
    assert "network" in text


@pytest.mark.parametrize(
    ("method", "expected"),
    [
        ("account/chatgptAuthTokens/refresh", "unsupported"),
        ("attestation/generate", "not negotiated"),
        ("applyPatchApproval", "not negotiated"),
        ("execCommandApproval", "not negotiated"),
    ],
)
@pytest.mark.asyncio
async def test_unsupported_and_not_negotiated_server_requests_send_errors(
    tmp_path: Path,
    method: str,
    expected: str,
) -> None:
    bridge, _bot, app_server, _store, _access = bridge_for(tmp_path)

    await bridge.handle_app_server_request(AppServerEvent(method, {}, request_id=700))

    assert app_server.error_responses[0]["request_id"] == 700
    assert app_server.error_responses[0]["code"] == -32601
    assert expected in app_server.error_responses[0]["error"]


@pytest.mark.parametrize(
    "method,params",
    [
        ("item/commandExecution/requestApproval", {"threadId": "missing", "turnId": "missing", "command": "pytest"}),
        ("item/fileChange/requestApproval", {"threadId": "missing", "turnId": "missing", "changes": []}),
        (
            "item/permissions/requestApproval",
            {
                "threadId": "missing",
                "turnId": "missing",
                "cwd": r"E:\Projects\codex-gateway",
                "permissions": {"network": {"enabled": True}},
            },
        ),
        (
            "mcpServer/elicitation/request",
            {"threadId": "missing", "turnId": "missing", "serverName": "honcho", "message": "Store?", "mode": "form"},
        ),
        (
            "item/tool/requestUserInput",
            {
                "threadId": "missing",
                "turnId": "missing",
                "questions": [{"id": "q", "header": "Question", "question": "Continue?"}],
            },
        ),
        ("item/tool/call", {"threadId": "missing", "turnId": "missing", "tool": "telegram_reply", "arguments": {}}),
    ],
)
@pytest.mark.asyncio
async def test_contextual_server_requests_without_turn_context_send_errors(
    tmp_path: Path,
    method: str,
    params: dict[str, Any],
) -> None:
    bridge, _bot, app_server, _store, _access = bridge_for(tmp_path)

    await bridge.handle_app_server_request(AppServerEvent(method, params, request_id=701))

    assert app_server.error_responses == [
        {"request_id": 701, "error": f"No active Telegram turn context is available for {method}.", "code": -32000}
    ]


@pytest.mark.asyncio
async def test_command_approval_request_and_accept_callback(tmp_path: Path) -> None:
    bridge, bot, app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update("run tests"))

    await bridge.handle_app_server_request(
        AppServerEvent(
            "item/commandExecution/requestApproval",
            {
                "threadId": "thr_1",
                "turnId": "turn_1",
                "command": "pytest",
                "cwd": str(bridge.settings.default_cwd),
            },
            request_id=77,
        )
    )

    pending = store.load_pending_approvals()
    token = next(iter(pending))
    markup = bot.messages[-1]["reply_markup"]
    assert "acceptForSession" not in str(markup)

    await bridge.handle_update(callback_update(f"approval:{token}:accept", message_id=bot.messages[-1]["message_id"]))

    assert app_server.approval_decisions == [{"request_id": 77, "decision": "accept"}]
    assert token not in store.load_pending_approvals()
    assert "accepted" in bot.edits[-1]["text"].lower()
    assert_callback_keyboard_cleared(bot.edits[-1])


@pytest.mark.parametrize(
    ("method", "params", "action"),
    [
        (
            "item/commandExecution/requestApproval",
            {"threadId": "thr_1", "turnId": "turn_1", "command": "pytest"},
            "decline",
        ),
        (
            "item/fileChange/requestApproval",
            {"threadId": "thr_1", "turnId": "turn_1", "changes": [{"path": "README.md", "action": "modify"}]},
            "cancel",
        ),
    ],
)
@pytest.mark.asyncio
async def test_command_and_file_approval_decline_and_cancel_callbacks_send_decisions(
    tmp_path: Path,
    method: str,
    params: dict[str, Any],
    action: str,
) -> None:
    bridge, bot, app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update("run tests"))

    await bridge.handle_app_server_request(AppServerEvent(method, params, request_id=78))
    token = next(iter(store.load_pending_approvals()))

    await bridge.handle_update(callback_update(f"approval:{token}:{action}", message_id=bot.messages[-1]["message_id"]))

    assert app_server.approval_decisions == [{"request_id": 78, "decision": action}]
    assert app_server.error_responses == []
    assert token not in store.load_pending_approvals()
    assert f"Approval {action}" in bot.answers[-1]["text"]


@pytest.mark.asyncio
async def test_permissions_approval_prompt_and_accept_callback_grants_turn_scope(tmp_path: Path) -> None:
    bridge, bot, app_server, store, access = bridge_for_typing(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update("needs permissions"))
    await asyncio.sleep(0)
    permissions = {
        "fileSystem": {"read": [str(bridge.settings.default_cwd)], "write": [str(bridge.settings.default_cwd / "out")]},
        "network": {"enabled": True},
    }

    await bridge.handle_app_server_request(
        AppServerEvent(
            "item/permissions/requestApproval",
            {
                "threadId": "thr_1",
                "turnId": "turn_1",
                "cwd": str(bridge.settings.default_cwd),
                "reason": "Need project write access",
                "permissions": permissions,
            },
            request_id=78,
        )
    )

    text = bot.messages[-1]["text"]
    assert "Permission approval requested" in text
    assert f"cwd: {bridge.settings.default_cwd}" in text
    assert "Need project write access" in text
    assert "file system" in text
    assert "network" in text
    assert bridge.typing_tasks == {}
    token = next(iter(store.load_pending_approvals()))

    await bridge.handle_update(callback_update(f"approval:{token}:accept", message_id=bot.messages[-1]["message_id"]))
    await asyncio.sleep(0)

    assert app_server.permission_approval_responses == [
        {"request_id": 78, "permissions": permissions, "scope": "turn"}
    ]
    assert token not in store.load_pending_approvals()
    assert set(bridge.typing_tasks) == {"turn_1"}
    assert_callback_keyboard_cleared(bot.edits[-1])


@pytest.mark.parametrize("action", ["decline", "cancel"])
@pytest.mark.asyncio
async def test_permissions_approval_decline_and_cancel_send_errors(
    tmp_path: Path,
    action: str,
) -> None:
    bridge, bot, app_server, store, access = bridge_for_typing(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update("needs permissions"))
    await bridge.handle_app_server_request(
        AppServerEvent(
            "item/permissions/requestApproval",
            {
                "threadId": "thr_1",
                "turnId": "turn_1",
                "cwd": str(bridge.settings.default_cwd),
                "permissions": {"network": {"enabled": True}},
            },
            request_id=79,
        )
    )
    token = next(iter(store.load_pending_approvals()))

    await bridge.handle_update(callback_update(f"approval:{token}:{action}", message_id=bot.messages[-1]["message_id"]))

    expected_action = "declined" if action == "decline" else "cancelled"
    assert app_server.error_responses == [
        {"request_id": 79, "error": f"Permission approval {expected_action}.", "code": -32000}
    ]
    assert app_server.permission_approval_responses == []
    assert token not in store.load_pending_approvals()
    assert bridge.typing_tasks == {}


@pytest.mark.asyncio
async def test_mcp_elicitation_request_sends_inline_prompt(tmp_path: Path) -> None:
    bridge, bot, _app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update("save memory"))

    await bridge.handle_app_server_request(
        AppServerEvent(
            "mcpServer/elicitation/request",
            {
                "serverName": "honcho",
                "threadId": "thr_1",
                "turnId": "turn_1",
                "message": "Allow Honcho to store this memory?",
                "mode": "form",
                "requestedSchema": {
                    "type": "object",
                    "properties": {
                        "confirmed": {"type": "boolean", "title": "Confirmed"},
                    },
                    "required": ["confirmed"],
                },
            },
            request_id=90,
        )
    )

    pending = store.load_pending_elicitations()
    token = next(iter(pending))
    message = bot.messages[-1]
    buttons = inline_button_map(message)
    assert "MCP input requested" in message["text"]
    assert "server: honcho" in message["text"]
    assert "Allow Honcho to store this memory?" in message["text"]
    assert buttons == {
        "Accept": f"elicitation:{token}:accept",
        "Decline": f"elicitation:{token}:decline",
        "Cancel": f"elicitation:{token}:cancel",
    }
    assert pending[token]["request_id"] == 90


@pytest.mark.asyncio
async def test_mcp_elicitation_accept_callback_sends_response(tmp_path: Path) -> None:
    bridge, bot, app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update("save memory"))
    await bridge.handle_app_server_request(
        AppServerEvent(
            "mcpServer/elicitation/request",
            {
                "serverName": "honcho",
                "threadId": "thr_1",
                "turnId": "turn_1",
                "message": "Allow Honcho to store this memory?",
                "mode": "form",
                "requestedSchema": {"type": "object", "properties": {}},
            },
            request_id=91,
        )
    )
    token = elicitation_token(inline_button_map(bot.messages[-1])["Accept"])

    await bridge.handle_update(callback_update(f"elicitation:{token}:accept", message_id=bot.messages[-1]["message_id"]))

    assert app_server.mcp_elicitation_responses == [{"request_id": 91, "action": "accept"}]
    assert token not in store.load_pending_elicitations()
    assert "accepted" in bot.edits[-1]["text"].lower()
    assert_callback_keyboard_cleared(bot.edits[-1])


@pytest.mark.parametrize("action", ["decline", "cancel"])
@pytest.mark.asyncio
async def test_mcp_elicitation_decline_and_cancel_callbacks_send_response(tmp_path: Path, action: str) -> None:
    bridge, bot, app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update("save memory"))
    await bridge.handle_app_server_request(
        AppServerEvent(
            "mcpServer/elicitation/request",
            {
                "serverName": "honcho",
                "threadId": "thr_1",
                "turnId": "turn_1",
                "message": "Allow Honcho to store this memory?",
                "mode": "form",
                "requestedSchema": {"type": "object", "properties": {}},
            },
            request_id=92,
        )
    )
    token = next(iter(store.load_pending_elicitations()))

    await bridge.handle_update(callback_update(f"elicitation:{token}:{action}", message_id=bot.messages[-1]["message_id"]))

    assert app_server.mcp_elicitation_responses == [{"request_id": 92, "action": action}]
    assert token not in store.load_pending_elicitations()
    assert action in bot.answers[-1]["text"].lower()


@pytest.mark.asyncio
async def test_mcp_elicitation_callback_rejects_other_user_and_expired_token(tmp_path: Path) -> None:
    now = datetime(2026, 5, 24, tzinfo=timezone.utc)
    bridge, bot, app_server, store, access = bridge_for(tmp_path, now=now)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update("save memory"))
    await bridge.handle_app_server_request(
        AppServerEvent(
            "mcpServer/elicitation/request",
            {
                "serverName": "honcho",
                "threadId": "thr_1",
                "turnId": "turn_1",
                "message": "Allow Honcho to store this memory?",
                "mode": "form",
                "requestedSchema": {"type": "object", "properties": {}},
            },
            request_id=93,
        )
    )
    token = next(iter(store.load_pending_elicitations()))

    await bridge.handle_update(
        callback_update(f"elicitation:{token}:decline", user_id=999, message_id=bot.messages[-1]["message_id"])
    )
    assert app_server.mcp_elicitation_responses == []
    assert "not allowed" in bot.answers[-1]["text"]

    pending = store.load_pending_elicitations()
    pending[token]["expires_at"] = (now - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    store.save_pending_elicitations(pending)
    await bridge.handle_update(callback_update(f"elicitation:{token}:cancel", message_id=bot.messages[-1]["message_id"]))

    assert app_server.mcp_elicitation_responses == []
    assert "expired" in bot.edits[-1]["text"].lower()


@pytest.mark.asyncio
async def test_tool_user_input_option_callback_sends_answer_and_resumes_typing(tmp_path: Path) -> None:
    bridge, bot, app_server, store, access = bridge_for_typing(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update("ask me"))
    await asyncio.sleep(0)
    await bridge.handle_app_server_request(
        AppServerEvent(
            "item/tool/requestUserInput",
            {
                "threadId": "thr_1",
                "turnId": "turn_1",
                "itemId": "item_1",
                "questions": [
                    {
                        "id": "backend",
                        "header": "Backend",
                        "question": "Which backend should I use?",
                        "options": [{"label": "App-server", "description": "Use Codex app-server."}],
                    }
                ],
            },
            request_id=94,
        )
    )

    message = bot.messages[-1]
    buttons = inline_button_map(message)
    token = user_input_token(buttons["App-server"])
    assert "Backend" in message["text"]
    assert "Which backend should I use?" in message["text"]
    assert "Use Codex app-server." in message["text"]
    assert "Other" in buttons
    assert "Cancel" in buttons
    assert bridge.typing_tasks == {}

    await bridge.handle_update(callback_update(buttons["App-server"], message_id=message["message_id"]))
    await asyncio.sleep(0)

    assert app_server.user_input_responses == [
        {"request_id": 94, "answers": {"backend": {"answers": ["App-server"]}}}
    ]
    assert token not in store.load_pending_user_inputs()
    assert set(bridge.typing_tasks) == {"turn_1"}
    assert_callback_keyboard_cleared(bot.edits[-1])


@pytest.mark.asyncio
async def test_tool_user_input_free_form_answer_uses_next_text_message(tmp_path: Path) -> None:
    bridge, bot, app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update("ask me"))
    await bridge.handle_app_server_request(
        AppServerEvent(
            "item/tool/requestUserInput",
            {
                "threadId": "thr_1",
                "turnId": "turn_1",
                "itemId": "item_1",
                "questions": [{"id": "answer", "header": "Details", "question": "What should I say?"}],
            },
            request_id=95,
        )
    )

    token = next(iter(store.load_pending_user_inputs()))
    assert "send your answer as a message" in bot.messages[-1]["text"].lower()

    await bridge.handle_update(message_update("Use the current repository.", message_id=11))

    assert app_server.user_input_responses == [
        {"request_id": 95, "answers": {"answer": {"answers": ["Use the current repository."]}}}
    ]
    assert token not in store.load_pending_user_inputs()
    assert len(app_server.turn_starts) == 1


@pytest.mark.asyncio
async def test_tool_user_input_other_button_waits_for_text_answer(tmp_path: Path) -> None:
    bridge, bot, app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update("ask me"))
    await bridge.handle_app_server_request(
        AppServerEvent(
            "item/tool/requestUserInput",
            {
                "threadId": "thr_1",
                "turnId": "turn_1",
                "itemId": "item_1",
                "questions": [
                    {
                        "id": "mode",
                        "header": "Mode",
                        "question": "Which mode?",
                        "options": [{"label": "Plan", "description": "Use plan mode."}],
                    }
                ],
            },
            request_id=96,
        )
    )
    buttons = inline_button_map(bot.messages[-1])
    token = user_input_token(buttons["Other"])

    await bridge.handle_update(callback_update(buttons["Other"], message_id=bot.messages[-1]["message_id"]))
    await bridge.handle_update(message_update("Code mode", message_id=12))

    assert app_server.user_input_responses == [
        {"request_id": 96, "answers": {"mode": {"answers": ["Code mode"]}}}
    ]
    assert token not in store.load_pending_user_inputs()


@pytest.mark.asyncio
async def test_tool_user_input_cancel_sends_error(tmp_path: Path) -> None:
    bridge, bot, app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update("ask me"))
    await bridge.handle_app_server_request(
        AppServerEvent(
            "item/tool/requestUserInput",
            {
                "threadId": "thr_1",
                "turnId": "turn_1",
                "itemId": "item_1",
                "questions": [{"id": "answer", "header": "Details", "question": "What should I say?"}],
            },
            request_id=97,
        )
    )
    buttons = inline_button_map(bot.messages[-1])
    token = user_input_token(buttons["Cancel"])

    await bridge.handle_update(callback_update(buttons["Cancel"], message_id=bot.messages[-1]["message_id"]))

    assert app_server.error_responses == [{"request_id": 97, "error": "User input cancelled.", "code": -32000}]
    assert token not in store.load_pending_user_inputs()


@pytest.mark.asyncio
async def test_tool_user_input_rejects_secret_questions(tmp_path: Path) -> None:
    bridge, _bot, app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update("ask me"))

    await bridge.handle_app_server_request(
        AppServerEvent(
            "item/tool/requestUserInput",
            {
                "threadId": "thr_1",
                "turnId": "turn_1",
                "itemId": "item_1",
                "questions": [
                    {"id": "token", "header": "Token", "question": "Enter the token.", "isSecret": True}
                ],
            },
            request_id=98,
        )
    )

    assert app_server.error_responses == [
        {"request_id": 98, "error": "Secret user input is not supported over Telegram.", "code": -32000}
    ]
    assert store.load_pending_user_inputs() == {}


@pytest.mark.asyncio
async def test_tool_user_input_callback_rejects_wrong_user_and_expires(tmp_path: Path) -> None:
    now = datetime(2026, 5, 24, tzinfo=timezone.utc)
    bridge, bot, app_server, store, access = bridge_for(tmp_path, now=now)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update("ask me"))
    await bridge.handle_app_server_request(
        AppServerEvent(
            "item/tool/requestUserInput",
            {
                "threadId": "thr_1",
                "turnId": "turn_1",
                "itemId": "item_1",
                "questions": [
                    {
                        "id": "backend",
                        "header": "Backend",
                        "question": "Which backend?",
                        "options": [{"label": "App-server", "description": "Use Codex app-server."}],
                    }
                ],
            },
            request_id=99,
        )
    )
    buttons = inline_button_map(bot.messages[-1])
    token = user_input_token(buttons["App-server"])

    await bridge.handle_update(
        callback_update(buttons["App-server"], user_id=999, message_id=bot.messages[-1]["message_id"])
    )
    assert app_server.user_input_responses == []
    assert app_server.error_responses == []
    assert "not allowed" in bot.answers[-1]["text"]

    pending = store.load_pending_user_inputs()
    pending[token]["expires_at"] = (now - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    store.save_pending_user_inputs(pending)
    await bridge.handle_update(callback_update(buttons["Cancel"], message_id=bot.messages[-1]["message_id"]))

    assert app_server.error_responses == [{"request_id": 99, "error": "User input expired.", "code": -32000}]
    assert token not in store.load_pending_user_inputs()
    assert "expired" in bot.edits[-1]["text"].lower()


@pytest.mark.asyncio
async def test_fork_uses_app_server_fork_when_current_thread_exists(tmp_path: Path) -> None:
    bridge, _bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update("hi"))
    await complete_active_turn(bridge)

    await bridge.handle_update(message_update("/fork", message_id=11))

    assert app_server.thread_forks == [
        {
            "thread_id": "thr_1",
            "exclude_turns": True,
            "developer_instructions": TELEGRAM_GATEWAY_DEVELOPER_INSTRUCTIONS,
        }
    ]
    assert app_server.turn_starts[-1]["thread_id"] == "thr_2"


@pytest.mark.asyncio
async def test_inline_side_turn_does_not_replace_active_thread_mapping(tmp_path: Path) -> None:
    bridge, bot, app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update("/new"))

    await bridge.handle_update(message_update("/side check this", message_id=11))

    assert app_server.thread_forks == [
        {
            "thread_id": "thr_1",
            "exclude_turns": True,
            "developer_instructions": TELEGRAM_GATEWAY_DEVELOPER_INSTRUCTIONS,
            "ephemeral": True,
            "cwd": str(bridge.settings.default_cwd),
            "approval_policy": "on-request",
            "sandbox": "workspace-write",
            "permissions": None,
        }
    ]
    assert app_server.turn_starts[-1]["thread_id"] == "thr_2"

    await bridge.handle_app_event(
        AppServerEvent(
            "item/completed",
            {"threadId": "thr_2", "turnId": "turn_1", "item": {"type": "agentMessage", "text": "SIDE"}},
        )
    )
    await bridge.handle_app_event(
        AppServerEvent(
            "turn/completed",
            {"threadId": "thr_2", "turn": {"id": "turn_1", "status": "completed", "items": []}},
        )
    )

    record = store.load_threads()[TelegramStateStore.thread_key("42", bridge.settings.default_cwd)]
    assert record["thread_id"] == "thr_1"
    assert bot.messages[-1]["text"] == "SIDE"


@pytest.mark.asyncio
async def test_archive_and_unarchive_use_app_server_thread_methods(tmp_path: Path) -> None:
    bridge, bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update("hi"))
    await complete_active_turn(bridge)

    await bridge.handle_update(message_update("/archive", message_id=11))
    await bridge.handle_update(message_update("/unarchive", message_id=12))

    assert app_server.thread_archives == [{"thread_id": "thr_1"}]
    assert app_server.thread_unarchives == [{"thread_id": "thr_1"}]
    assert app_server.thread_resumes == [
        {
            "thread_id": "thr_1",
            "cwd": str(bridge.settings.default_cwd),
            "approval_policy": "on-request",
            "sandbox": "workspace-write",
            "model": None,
            "permissions": None,
            "developer_instructions": TELEGRAM_GATEWAY_DEVELOPER_INSTRUCTIONS,
        }
    ]
    assert [message["text"] for message in bot.messages[-2:]] == ["Thread archived.", "Thread unarchived."]


@pytest.mark.asyncio
async def test_thread_command_json_rpc_errors_are_reported_without_crashing(tmp_path: Path) -> None:
    bridge, bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update("hi"))
    await complete_active_turn(bridge)

    async def failing_unarchive(**_kwargs: Any) -> dict[str, Any]:
        raise JsonRpcError("failed to unarchive thread")

    app_server.thread_unarchive = failing_unarchive

    await bridge.handle_update(message_update("/unarchive", message_id=11))

    assert bot.messages[-1]["text"] == "App-server command failed: failed to unarchive thread"


@pytest.mark.asyncio
async def test_compact_uses_app_server_compact_when_thread_exists(tmp_path: Path) -> None:
    bridge, _bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update("hi"))
    await complete_active_turn(bridge)

    await bridge.handle_update(message_update("/compact", message_id=11))

    assert app_server.compactions == [{"thread_id": "thr_1"}]


@pytest.mark.asyncio
async def test_codex_turn_command_json_rpc_errors_are_reported_without_crashing(tmp_path: Path) -> None:
    bridge, bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update("hi"))
    await complete_active_turn(bridge)

    async def failing_compact(**_kwargs: Any) -> dict[str, Any]:
        raise JsonRpcError("thread not found: thr_1")

    app_server.thread_compact_start = failing_compact

    await bridge.handle_update(message_update("/compact", message_id=11))

    assert bot.messages[-1]["text"] == "App-server command failed: thread not found: thr_1"


@pytest.mark.asyncio
async def test_review_uses_app_server_review_start_when_thread_exists(tmp_path: Path) -> None:
    bridge, _bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update("hi"))
    await complete_active_turn(bridge)

    await bridge.handle_update(message_update("/review", message_id=11))

    assert app_server.reviews == [{"thread_id": "thr_1", "target": {"type": "uncommittedChanges"}}]
    assert bridge.turns["turn_review_2"].thread_id == "thr_1"


@pytest.mark.asyncio
async def test_review_creates_thread_and_uses_app_server_review_start_without_existing_thread(tmp_path: Path) -> None:
    bridge, _bot, app_server, store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")

    await bridge.handle_update(message_update("/review"))

    key = TelegramStateStore.thread_key(42, bridge.settings.default_cwd)
    assert store.load_threads()[key]["thread_id"] == "thr_1"
    assert app_server.turn_starts == []
    assert app_server.reviews == [{"thread_id": "thr_1", "target": {"type": "uncommittedChanges"}}]
    assert bridge.turns["turn_review_1"].thread_id == "thr_1"


@pytest.mark.asyncio
async def test_approval_callback_rejects_other_user_and_expired_token(tmp_path: Path) -> None:
    now = datetime(2026, 5, 24, tzinfo=timezone.utc)
    bridge, bot, app_server, store, access = bridge_for(tmp_path, now=now)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update("run tests"))
    await bridge.handle_app_server_request(
        AppServerEvent(
            "item/commandExecution/requestApproval",
            {"threadId": "thr_1", "turnId": "turn_1", "command": "pytest"},
            request_id=77,
        )
    )
    token = next(iter(store.load_pending_approvals()))

    await bridge.handle_update(callback_update(f"approval:{token}:decline", user_id=999, message_id=bot.messages[-1]["message_id"]))
    assert app_server.approval_decisions == []
    assert "not allowed" in bot.answers[-1]["text"]

    pending = store.load_pending_approvals()
    pending[token]["expires_at"] = (now - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    store.save_pending_approvals(pending)
    await bridge.handle_update(callback_update(f"approval:{token}:cancel", message_id=bot.messages[-1]["message_id"]))

    assert app_server.approval_decisions == []
    assert "expired" in bot.edits[-1]["text"].lower()


@pytest.mark.asyncio
async def test_telegram_reply_tool_suppresses_final_auto_reply(tmp_path: Path) -> None:
    bridge, bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update("hi"))

    await bridge.handle_app_server_request(
        AppServerEvent(
            "item/tool/call",
            {"turnId": "turn_1", "tool": "telegram_reply", "arguments": {"text": "sent by tool"}},
            request_id=80,
        )
    )
    await bridge.handle_app_event(
        AppServerEvent("item/completed", {"turnId": "turn_1", "item": {"type": "agent_message", "text": "final"}})
    )
    await bridge.handle_app_event(AppServerEvent("turn/completed", {"turnId": "turn_1"}))

    assert [message["text"] for message in bot.messages] == ["sent by tool"]
    assert app_server.tool_results[0]["content"][0]["text"] == "sent message_ids=1001"


@pytest.mark.asyncio
async def test_telegram_reply_tool_sends_valid_message_options(tmp_path: Path) -> None:
    bridge, bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update("hi"))

    await bridge.handle_app_server_request(
        AppServerEvent(
            "item/tool/call",
            {
                "turnId": "turn_1",
                "tool": "telegram_reply",
                "arguments": {
                    "text": "<b>done</b>",
                    "parse_mode": "HTML",
                    "reply_to_message_id": 10,
                },
            },
            request_id=86,
        )
    )

    assert bot.messages[-1] == {
        "message_id": 1001,
        "chat": {"id": 42},
        "text": "<b>done</b>",
        "parse_mode": "HTML",
        "reply_to_message_id": 10,
    }
    assert app_server.tool_results[-1]["content"] == [{"type": "text", "text": "sent message_ids=1001"}]


@pytest.mark.asyncio
async def test_telegram_react_and_edit_tools_are_scoped(tmp_path: Path) -> None:
    bridge, bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update("hi"))

    sent = await bot.send_message(42, "owned")
    bridge.track_bridge_message(42, sent[0]["message_id"])
    await bridge.handle_app_server_request(
        AppServerEvent(
            "item/tool/call",
            {"turnId": "turn_1", "tool": "telegram_react", "arguments": {"emoji": "👍", "message_id": 10}},
            request_id=81,
        )
    )
    await bridge.handle_app_server_request(
        AppServerEvent(
            "item/tool/call",
            {"turnId": "turn_1", "tool": "telegram_edit_message", "arguments": {"message_id": sent[0]["message_id"], "text": "edited"}},
            request_id=82,
        )
    )
    await bridge.handle_app_server_request(
        AppServerEvent(
            "item/tool/call",
            {"turnId": "turn_1", "tool": "telegram_edit_message", "arguments": {"message_id": 9999, "text": "bad"}},
            request_id=83,
        )
    )

    assert bot.reactions == [{"chat_id": 42, "message_id": 10, "emoji": "👍"}]
    assert bot.edits[-1]["text"] == "edited"
    assert "not bridge-owned" in app_server.tool_results[-1]["content"][0]["text"]


@pytest.mark.asyncio
async def test_download_attachment_tool_is_limited_to_current_turn_files(tmp_path: Path) -> None:
    bridge, bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    bot.files["file_1"] = {"file_path": "docs/note.txt", "file_size": 4}
    bot.downloads["docs/note.txt"] = b"note"
    update = message_update("summarize")
    update["message"]["document"] = {"file_id": "file_1", "file_name": "note.txt", "file_size": 4}
    await bridge.handle_update(update)

    await bridge.handle_app_server_request(
        AppServerEvent(
            "item/tool/call",
            {"turnId": "turn_1", "tool": "telegram_download_attachment", "arguments": {"file_id": "file_1"}},
            request_id=84,
        )
    )
    await bridge.handle_app_server_request(
        AppServerEvent(
            "item/tool/call",
            {"turnId": "turn_1", "tool": "telegram_download_attachment", "arguments": {"file_id": "file_2"}},
            request_id=85,
        )
    )

    assert "note.txt" in app_server.tool_results[-2]["content"][0]["text"]
    assert "not available" in app_server.tool_results[-1]["content"][0]["text"]


@pytest.mark.asyncio
async def test_send_document_tool_sends_workspace_file_to_telegram(tmp_path: Path) -> None:
    bridge, bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    output = bridge.settings.default_cwd / "report.xlsx"
    output.write_bytes(b"xlsx bytes")
    await bridge.handle_update(message_update("make a report"))

    await bridge.handle_app_server_request(
        AppServerEvent(
            "item/tool/call",
            {
                "turnId": "turn_1",
                "tool": "telegram_send_document",
                "arguments": {
                    "path": "report.xlsx",
                    "caption": "Report",
                    "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                },
            },
            request_id=86,
        )
    )
    await bridge.handle_app_event(
        AppServerEvent("item/completed", {"turnId": "turn_1", "item": {"type": "agent_message", "text": "final"}})
    )
    await bridge.handle_app_event(AppServerEvent("turn/completed", {"turnId": "turn_1"}))

    assert bot.documents[-1]["filename"] == "report.xlsx"
    assert bot.documents[-1]["document"] == b"xlsx bytes"
    assert bot.documents[-1]["caption"] == "Report"
    assert bot.documents[-1]["content_type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert app_server.tool_results[-1]["content"][0]["text"].startswith("sent message_id=")
    assert [message["text"] for message in bot.messages] == []


@pytest.mark.asyncio
async def test_media_tools_send_workspace_files_to_native_telegram_methods(tmp_path: Path) -> None:
    bridge, bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    photo = bridge.settings.default_cwd / "photo.png"
    video = bridge.settings.default_cwd / "clip.mp4"
    photo.write_bytes(b"png bytes")
    video.write_bytes(b"mp4 bytes")
    await bridge.handle_update(message_update("send media"))

    await bridge.handle_app_server_request(
        AppServerEvent(
            "item/tool/call",
            {
                "turnId": "turn_1",
                "tool": "telegram_send_photo",
                "arguments": {"path": "photo.png", "caption": "Photo", "filename": "final.png"},
            },
            request_id=88,
        )
    )
    await bridge.handle_app_server_request(
        AppServerEvent(
            "item/tool/call",
            {
                "turnId": "turn_1",
                "tool": "telegram_send_video",
                "arguments": {
                    "path": "clip.mp4",
                    "caption": "Clip",
                    "duration": 5,
                    "width": 640,
                    "height": 360,
                },
            },
            request_id=89,
        )
    )

    assert bot.photos[-1]["filename"] == "final.png"
    assert bot.photos[-1]["photo"] == b"png bytes"
    assert bot.photos[-1]["caption"] == "Photo"
    assert bot.photos[-1]["content_type"] == "image/png"
    assert bot.videos[-1]["filename"] == "clip.mp4"
    assert bot.videos[-1]["video"] == b"mp4 bytes"
    assert bot.videos[-1]["caption"] == "Clip"
    assert bot.videos[-1]["content_type"] == "video/mp4"
    assert bot.videos[-1]["duration"] == 5
    assert bot.videos[-1]["width"] == 640
    assert bot.videos[-1]["height"] == 360
    assert app_server.tool_results[-2]["content"][0]["text"].startswith("sent message_id=")
    assert app_server.tool_results[-1]["content"][0]["text"].startswith("sent message_id=")


@pytest.mark.parametrize(
    ("tool", "filename", "data", "collection_name", "media_key", "arguments", "expected"),
    [
        (
            "telegram_send_animation",
            "loop.gif",
            b"gif bytes",
            "animations",
            "animation",
            {"caption": "Loop", "duration": 2, "width": 320, "height": 180},
            {"caption": "Loop", "duration": 2, "width": 320, "height": 180},
        ),
        (
            "telegram_send_audio",
            "song.mp3",
            b"mp3 bytes",
            "audios",
            "audio",
            {"caption": "Song", "duration": 30, "performer": "Ada", "title": "Theme"},
            {"caption": "Song", "duration": 30, "performer": "Ada", "title": "Theme"},
        ),
        (
            "telegram_send_voice",
            "voice.ogg",
            b"ogg bytes",
            "voices",
            "voice",
            {"caption": "Voice", "duration": 5},
            {"caption": "Voice", "duration": 5},
        ),
        (
            "telegram_send_video_note",
            "note.mp4",
            b"note bytes",
            "video_notes",
            "video_note",
            {"duration": 4, "length": 240},
            {"duration": 4, "length": 240},
        ),
        (
            "telegram_send_sticker",
            "sticker.webp",
            b"webp bytes",
            "stickers",
            "sticker",
            {"emoji": ":)"},
            {"emoji": ":)"},
        ),
    ],
)
@pytest.mark.asyncio
async def test_additional_media_tools_send_workspace_files_to_native_methods(
    tmp_path: Path,
    tool: str,
    filename: str,
    data: bytes,
    collection_name: str,
    media_key: str,
    arguments: dict[str, Any],
    expected: dict[str, Any],
) -> None:
    bridge, bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    path = bridge.settings.default_cwd / filename
    path.write_bytes(data)
    await bridge.handle_update(message_update("send media"))

    await bridge.handle_app_server_request(
        AppServerEvent(
            "item/tool/call",
            {"turnId": "turn_1", "tool": tool, "arguments": {"path": filename, **arguments}},
            request_id=188,
        )
    )

    sent = getattr(bot, collection_name)[-1]
    assert sent["filename"] == filename
    assert sent[media_key] == data
    for key, value in expected.items():
        assert sent[key] == value
    assert app_server.tool_results[-1]["content"][0]["text"].startswith("sent message_id=")


@pytest.mark.asyncio
async def test_ambiguous_media_send_timeout_suppresses_exact_retry(tmp_path: Path) -> None:
    bridge, bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    path = bridge.settings.default_cwd / "loop.gif"
    path.write_bytes(b"gif bytes")
    await bridge.handle_update(message_update("send media"))
    calls = 0

    async def timeout_send_animation(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        raise TelegramAPIError("Telegram API sendAnimation failed: ReadTimeout", ambiguous_delivery=True)

    bot.send_animation = timeout_send_animation  # type: ignore[method-assign]
    event = AppServerEvent(
        "item/tool/call",
        {
            "turnId": "turn_1",
            "tool": "telegram_send_animation",
            "arguments": {"path": "loop.gif", "caption": "Funny GIF"},
        },
        request_id=188,
    )

    await bridge.handle_app_server_request(event)
    await bridge.handle_app_server_request(
        AppServerEvent(event.method, event.params, request_id=189)
    )

    assert calls == 1
    assert "timed out after it was submitted" in app_server.tool_results[-2]["content"][0]["text"]
    assert "Duplicate Telegram send suppressed" in app_server.tool_results[-1]["content"][0]["text"]


@pytest.mark.asyncio
async def test_live_photo_media_group_and_paid_media_tools_send_native_payloads(tmp_path: Path) -> None:
    bridge, bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    for name, data in {
        "live.mp4": b"live video",
        "still.jpg": b"still",
        "photo.jpg": b"photo",
        "clip.mp4": b"clip",
        "paid.mp4": b"paid",
    }.items():
        (bridge.settings.default_cwd / name).write_bytes(data)
    await bridge.handle_update(message_update("send rich media"))

    await bridge.handle_app_server_request(
        AppServerEvent(
            "item/tool/call",
            {
                "turnId": "turn_1",
                "tool": "telegram_send_live_photo",
                "arguments": {"live_photo_path": "live.mp4", "photo_path": "still.jpg", "caption": "Live"},
            },
            request_id=189,
        )
    )
    await bridge.handle_app_server_request(
        AppServerEvent(
            "item/tool/call",
            {
                "turnId": "turn_1",
                "tool": "telegram_send_media_group",
                "arguments": {
                    "media": [
                        {"type": "photo", "path": "photo.jpg", "caption": "A"},
                        {"type": "video", "path": "clip.mp4", "duration": 3},
                    ]
                },
            },
            request_id=190,
        )
    )
    await bridge.handle_app_server_request(
        AppServerEvent(
            "item/tool/call",
            {
                "turnId": "turn_1",
                "tool": "telegram_send_paid_media",
                "arguments": {"star_count": 5, "media": [{"type": "video", "path": "paid.mp4"}], "caption": "Paid"},
            },
            request_id=191,
        )
    )

    assert bot.live_photos[-1]["live_photo"] == b"live video"
    assert bot.live_photos[-1]["photo"] == b"still"
    assert bot.live_photos[-1]["caption"] == "Live"
    assert bot.media_groups[-1]["media"][0]["media"] == "attach://media0"
    assert bot.media_groups[-1]["files"]["media0"][1] == b"photo"
    assert bot.media_groups[-1]["files"]["media1"][1] == b"clip"
    assert bot.paid_media[-1]["star_count"] == 5
    assert bot.paid_media[-1]["media"][0]["type"] == "video"
    assert bot.paid_media[-1]["files"]["media0"][1] == b"paid"
    result_texts = [result["content"][0]["text"] for result in app_server.tool_results[-3:]]
    assert result_texts[0].startswith("sent message_id=")
    assert re.fullmatch(r"sent message_ids=\d+,\d+", result_texts[1])
    assert result_texts[2].startswith("sent message_id=")


@pytest.mark.parametrize(
    ("tool", "filename", "label", "collection_name"),
    [
        ("telegram_send_photo", "photo.png", "Photo", "photos"),
        ("telegram_send_video", "clip.mp4", "Video", "videos"),
    ],
)
@pytest.mark.asyncio
async def test_media_tools_reject_missing_outside_and_oversized_paths(
    tmp_path: Path,
    tool: str,
    filename: str,
    label: str,
    collection_name: str,
) -> None:
    bridge, bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    object.__setattr__(bridge.settings, "max_attachment_bytes", 3)
    outside = tmp_path / filename
    outside.write_bytes(b"ok")
    oversized = bridge.settings.default_cwd / filename
    oversized.write_bytes(b"tool")
    await bridge.handle_update(message_update("send media"))

    for request_id, arguments in enumerate(
        (
            {},
            {"path": str(outside)},
            {"path": filename},
        ),
        start=90,
    ):
        await bridge.handle_app_server_request(
            AppServerEvent(
                "item/tool/call",
                {"turnId": "turn_1", "tool": tool, "arguments": arguments},
                request_id=request_id,
            )
        )

    assert getattr(bot, collection_name) == []
    results = [item["content"][0]["text"] for item in app_server.tool_results[-3:]]
    assert results[0] == f"{label} path is required."
    assert "outside the active workspace" in results[1]
    assert results[2] == f"{label} is too large for this bridge."


@pytest.mark.asyncio
async def test_send_document_tool_rejects_files_outside_active_workspace(tmp_path: Path) -> None:
    bridge, bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    await bridge.handle_update(message_update("send this"))

    await bridge.handle_app_server_request(
        AppServerEvent(
            "item/tool/call",
            {"turnId": "turn_1", "tool": "telegram_send_document", "arguments": {"path": str(outside)}},
            request_id=87,
        )
    )

    assert bot.documents == []
    assert "outside the active workspace" in app_server.tool_results[-1]["content"][0]["text"]


@pytest.mark.asyncio
async def test_send_file_tools_allow_current_turn_downloaded_attachment_paths(tmp_path: Path) -> None:
    bridge, bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    bot.files["file_1"] = {"file_path": "docs/note.txt", "file_size": 4}
    bot.downloads["docs/note.txt"] = b"note"
    update = message_update("send it back")
    update["message"]["document"] = {"file_id": "file_1", "file_name": "note.txt", "file_size": 4}
    await bridge.handle_update(update)
    context = bridge._active_turn_context("42")
    assert context is not None
    downloaded_path = context.attachments["file_1"]["path"]

    await bridge.handle_app_server_request(
        AppServerEvent(
            "item/tool/call",
            {"turnId": "turn_1", "tool": "telegram_send_document", "arguments": {"path": downloaded_path}},
            request_id=192,
        )
    )

    assert bot.documents[-1]["filename"] == "note.txt"
    assert bot.documents[-1]["document"] == b"note"
    assert app_server.tool_results[-1]["content"][0]["text"].startswith("sent message_id=")


@pytest.mark.asyncio
async def test_structured_send_tools_route_to_native_bot_methods(tmp_path: Path) -> None:
    bridge, bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update("send structured payloads"))

    calls = [
        ("telegram_send_contact", {"phone_number": "+15551212", "first_name": "Ada", "last_name": "Lovelace"}),
        ("telegram_send_location", {"latitude": 14.6, "longitude": 121.0, "horizontal_accuracy": 12.5}),
        ("telegram_send_venue", {"latitude": 14.6, "longitude": 121.0, "title": "HQ", "address": "Main St"}),
        ("telegram_send_poll", {"question": "Ship?", "options": ["Yes", "No"], "is_anonymous": False}),
        ("telegram_send_checklist", {"business_connection_id": "biz_1", "title": "Launch", "tasks": ["Test", {"id": 9, "text": "Ship"}]}),
        ("telegram_send_dice", {"emoji": "🎲"}),
    ]
    for index, (tool, arguments) in enumerate(calls, start=193):
        await bridge.handle_app_server_request(
            AppServerEvent(
                "item/tool/call",
                {"turnId": "turn_1", "tool": tool, "arguments": arguments},
                request_id=index,
            )
        )

    assert bot.contacts[-1]["phone_number"] == "+15551212"
    assert bot.locations[-1]["latitude"] == 14.6
    assert bot.venues[-1]["title"] == "HQ"
    assert bot.polls[-1]["poll"]["question"] == "Ship?"
    assert bot.checklists[-1]["business_connection_id"] == "biz_1"
    assert bot.checklists[-1]["checklist"]["tasks"][1] == {"id": 9, "text": "Ship"}
    assert bot.dice[-1]["dice"]["emoji"] == "🎲"
    assert all(result["content"][0]["text"].startswith("sent message_id=") for result in app_server.tool_results[-6:])
    assert "poll_id=poll_" in app_server.tool_results[-3]["content"][0]["text"]
    assert "dice_value=3" in app_server.tool_results[-1]["content"][0]["text"]


@pytest.mark.asyncio
async def test_copy_and_forward_tools_are_scoped_to_current_inbound_message(tmp_path: Path) -> None:
    bridge, bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    await bridge.handle_update(message_update("copy this", message_id=44))

    await bridge.handle_app_server_request(
        AppServerEvent(
            "item/tool/call",
            {"turnId": "turn_1", "tool": "telegram_copy_current_message", "arguments": {"caption": "Copied"}},
            request_id=199,
        )
    )
    await bridge.handle_app_server_request(
        AppServerEvent(
            "item/tool/call",
            {"turnId": "turn_1", "tool": "telegram_forward_current_message", "arguments": {}},
            request_id=200,
        )
    )

    assert bot.copied_messages[-1]["chat_id"] == 42
    assert bot.copied_messages[-1]["from_chat_id"] == 42
    assert bot.copied_messages[-1]["source_message_id"] == 44
    assert bot.copied_messages[-1]["caption"] == "Copied"
    assert bot.forwarded_messages[-1]["source_message_id"] == 44
    assert app_server.tool_results[-2]["content"][0]["text"].startswith("sent message_id=")
    assert app_server.tool_results[-1]["content"][0]["text"].startswith("sent message_id=")


@pytest.mark.asyncio
async def test_telegram_api_tool_errors_are_returned_as_tool_results(tmp_path: Path) -> None:
    bridge, bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")
    paid = bridge.settings.default_cwd / "paid.mp4"
    paid.write_bytes(b"paid")
    await bridge.handle_update(message_update("send paid"))

    async def failing_paid_media(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise TelegramAPIError("Telegram API sendPaidMedia failed: 400 account is restricted")

    bot.send_paid_media = failing_paid_media  # type: ignore[method-assign]
    await bridge.handle_app_server_request(
        AppServerEvent(
            "item/tool/call",
            {
                "turnId": "turn_1",
                "tool": "telegram_send_paid_media",
                "arguments": {"star_count": 5, "media": [{"type": "video", "path": "paid.mp4"}]},
            },
            request_id=201,
        )
    )

    assert "Telegram API error" in app_server.tool_results[-1]["content"][0]["text"]
    assert "account is restricted" in app_server.tool_results[-1]["content"][0]["text"]


@pytest.mark.asyncio
async def test_skill_marker_adds_skill_input_when_app_server_lookup_finds_path(tmp_path: Path) -> None:
    bridge, _bot, app_server, _store, access = bridge_for(tmp_path)
    access.allow_user("123", username="gatewayuser", source="cli")

    await bridge.handle_update(message_update("$skill-creator Add triage steps"))

    assert app_server.skills == [{"cwds": [str(bridge.settings.default_cwd)], "force_reload": False}]
    assert app_server.turn_starts[0]["input_items"][1] == {
        "type": "skill",
        "name": "skill-creator",
        "path": r"C:\Users\gatewayuser\.codex\skills\skill-creator\SKILL.md",
    }


def test_initial_poll_offset_uses_max_recorded_update_id(tmp_path: Path) -> None:
    from codex_gateway.gateways.telegram.bridge import initial_poll_offset

    store = TelegramStateStore(tmp_path)
    store.save_chats(
        {
            "chat_id:1": {"last_update_id": 10},
            "chat_id:2": {"last_update_id": 12},
        }
    )

    assert initial_poll_offset(store) == 13


def test_thread_sandbox_value_normalizes_cli_style_default() -> None:
    from codex_gateway.gateways.telegram.bridge import _approval_policy_value, _thread_sandbox_value

    assert _thread_sandbox_value("workspace-write") == "workspace-write"
    assert _thread_sandbox_value("workspaceWrite") == "workspace-write"
    assert _thread_sandbox_value("read-only") == "read-only"
    assert _thread_sandbox_value("readOnly") == "read-only"
    assert _thread_sandbox_value("dangerFullAccess") == "danger-full-access"
    assert _approval_policy_value("unlessTrusted") == "on-request"


def test_dynamic_tool_names_are_responses_api_safe() -> None:
    from codex_gateway.gateways.telegram.bridge import _tool_name, telegram_dynamic_tools

    names = [tool["name"] for tool in telegram_dynamic_tools()]

    assert names == [
        "telegram_reply",
        "telegram_react",
        "telegram_edit_message",
        "telegram_download_attachment",
        "telegram_send_photo",
        "telegram_send_video",
        "telegram_send_document",
        "telegram_send_animation",
        "telegram_send_audio",
        "telegram_send_voice",
        "telegram_send_video_note",
        "telegram_send_sticker",
        "telegram_send_live_photo",
        "telegram_send_media_group",
        "telegram_send_paid_media",
        "telegram_send_contact",
        "telegram_send_location",
        "telegram_send_venue",
        "telegram_send_poll",
        "telegram_send_checklist",
        "telegram_send_dice",
        "telegram_copy_current_message",
        "telegram_forward_current_message",
    ]
    assert all("." not in name for name in names)
    assert _tool_name("telegram.reply") == "telegram_reply"
    assert _tool_name("telegram.send_photo") == "telegram_send_photo"
    assert _tool_name("telegram.send_video") == "telegram_send_video"
    assert _tool_name("telegram.send_document") == "telegram_send_document"
    assert _tool_name("telegram.send_paid_media") == "telegram_send_paid_media"
    assert _tool_name("telegram.copy_current_message") == "telegram_copy_current_message"

