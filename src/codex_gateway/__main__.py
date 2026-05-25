from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .gateways.telegram.access import AccessManager
from .gateways.telegram.bot_api import TelegramAPIError, TelegramBotAPI
from .gateways.telegram.bridge import run_telegram_bridge, telegram_status_summary
from .gateways.telegram.config import TelegramSettingsError, get_telegram_settings
from .gateways.telegram.setup import run_telegram_setup
from .gateways.telegram.state import TelegramStateStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codex-gateway")
    subparsers = parser.add_subparsers(dest="command")
    _add_telegram_parser(subparsers)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()

    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    if args.command == "telegram":
        _run_telegram_command(args)
        return
    raise SystemExit("Choose a command: telegram")


def _add_telegram_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    telegram_parser = subparsers.add_parser("telegram")
    telegram_subparsers = telegram_parser.add_subparsers(dest="telegram_command")
    telegram_subparsers.add_parser("run")
    setup_parser = telegram_subparsers.add_parser("setup")
    setup_parser.add_argument("--bot-token")
    setup_parser.add_argument("--user-id")
    setup_parser.add_argument("--allowed-root")
    setup_parser.add_argument("--default-cwd")
    setup_parser.add_argument("--state-dir")
    setup_parser.add_argument("--permission-profile")
    setup_parser.add_argument("--env-file", default=".env")
    telegram_subparsers.add_parser("status")

    access_parser = telegram_subparsers.add_parser("access")
    access_subparsers = access_parser.add_subparsers(dest="access_command")
    access_subparsers.add_parser("status")
    pair_parser = access_subparsers.add_parser("pair")
    pair_parser.add_argument("code")
    for command in ("allow", "remove"):
        parser = access_subparsers.add_parser(command)
        parser.add_argument("--user-id", required=True)
        if command == "allow":
            parser.add_argument("--username", default=None)

    workspace_parser = telegram_subparsers.add_parser("workspace")
    workspace_subparsers = workspace_parser.add_subparsers(dest="workspace_command")
    workspace_subparsers.add_parser("list")


def _run_telegram_command(args: argparse.Namespace) -> None:
    if args.telegram_command == "setup":
        run_telegram_setup(args)
        return
    try:
        settings = get_telegram_settings()
    except TelegramSettingsError as exc:
        raise SystemExit(str(exc)) from exc
    store = TelegramStateStore(settings.state_dir)

    if args.telegram_command == "run":
        asyncio.run(run_telegram_bridge())
        return
    if args.telegram_command == "status":
        print(json.dumps(telegram_status_summary(settings, store), indent=2))
        return
    if args.telegram_command == "access":
        _run_telegram_access_command(args, store, settings)
        return
    if args.telegram_command == "workspace":
        _run_telegram_workspace_command(args, settings)
        return
    raise SystemExit("Choose a telegram command: run, setup, status, access, workspace")


def _run_telegram_access_command(args: argparse.Namespace, store: TelegramStateStore, settings: object) -> None:
    manager = AccessManager(store, allowed_user_id=getattr(settings, "allowed_user_id", None))
    if args.access_command == "status":
        access = store.load_access()
        result = {
            "allowed_users": sorted(access.get("allowed_users", {}).keys()),
            "pairing_codes": len(access.get("pairing_codes", {})),
        }
    elif args.access_command == "pair":
        paired = manager.consume_pairing_code(args.code)
        if paired is None:
            raise SystemExit("Pairing code is invalid or expired.")
        notification = _notify_pairing_success(settings, paired)
        result = {
            "user_id": paired["user_id"],
            "paired": True,
            "telegram_notified": notification["sent"],
        }
        if notification["error"]:
            result["telegram_notification_error"] = notification["error"]
    elif args.access_command == "allow":
        try:
            manager.allow_user(args.user_id, username=args.username, source="cli")
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        result = {"user_id": str(args.user_id), "allowed": True}
    elif args.access_command == "remove":
        result = {"user_id": str(args.user_id), "removed": manager.remove_user(args.user_id)}
    else:
        raise SystemExit("Choose a telegram access command: status, pair, allow, remove")
    print(json.dumps(result, indent=2))


def _notify_pairing_success(settings: object, paired: dict[str, str]) -> dict[str, object]:
    bot_token = getattr(settings, "bot_token", None)
    chat_id = paired.get("chat_id")
    if not bot_token or not chat_id:
        return {"sent": False, "error": None}
    try:
        asyncio.run(_send_pairing_success_message(str(bot_token), chat_id))
    except TelegramAPIError as exc:
        return {"sent": False, "error": str(exc)}
    return {"sent": True, "error": None}


async def _send_pairing_success_message(bot_token: str, chat_id: str) -> None:
    bot = TelegramBotAPI(bot_token)
    try:
        await bot.send_message(chat_id, "Pairing complete. You can now send messages here to use Codex Gateway.")
    finally:
        await bot.aclose()


def _run_telegram_workspace_command(args: argparse.Namespace, settings: object) -> None:
    if args.workspace_command != "list":
        raise SystemExit("Choose a telegram workspace command: list")
    result = {
        "allowed_roots": [str(root) for root in settings.allowed_roots],  # type: ignore[attr-defined]
        "default_cwd": str(settings.default_cwd),  # type: ignore[attr-defined]
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
