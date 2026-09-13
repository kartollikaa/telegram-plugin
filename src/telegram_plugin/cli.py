"""Machine-readable command-line adapter for TelegramApplication."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from telegram_plugin.config import Config, load_config_from_environment
from telegram_plugin.errors import TelegramPluginError, describe
from telegram_plugin.matching import normalize_dialog_text
from telegram_plugin.paths import read_confined_text
from telegram_plugin.render import DEFAULT_ITEMS, MAX_ITEMS

if TYPE_CHECKING:
    from telegram_plugin.application import TelegramApplication
    from telegram_plugin.client import TelegramGateway

EXPORT_LIMIT = 5000
GatewayFactory = Callable[[Config], "TelegramGateway"]


def _bounded_integer(maximum: int):
    def parse(raw: str) -> int:
        try:
            value = int(raw)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("must be an integer") from exc
        if not 1 <= value <= maximum:
            raise argparse.ArgumentTypeError(f"must be between 1 and {maximum}")
        return value

    return parse


limit_value = _bounded_integer(MAX_ITEMS)
export_limit_value = _bounded_integer(EXPORT_LIMIT)
positive_integer = _bounded_integer(2**63 - 1)
discovery_limit = _bounded_integer(50)
cursor_integer = _bounded_integer(2**63 - 1)


def _discovery_query(raw: str) -> str:
    if not normalize_dialog_text(raw):
        raise argparse.ArgumentTypeError("query must not be empty")
    return raw


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="telegram", description="Telegram access for local agents")
    parser.add_argument("--version", action="version", version=f"%(prog)s {_plugin_version()}")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("whoami", help="show the authorized Telegram account")

    dialogs = commands.add_parser("dialogs", help="list chats, optionally filtered by title")
    dialogs.add_argument("--query", help="substring of the chat title")
    dialogs.add_argument(
        "--limit", type=limit_value, default=DEFAULT_ITEMS, help=f"1..{MAX_ITEMS}"
    )

    find_chat = commands.add_parser("find-chat", help="find chats by remembered metadata")
    find_chat.add_argument("query", help="a remembered name, partial and case-insensitive", type=_discovery_query)
    find_chat.add_argument("--limit", type=discovery_limit, default=10, help="1..50")

    resolve = commands.add_parser("resolve", help="resolve an exact chat reference")
    resolve.add_argument("chat", help="t.me link, @username or numeric id")

    message = commands.add_parser("message", help="read one exact message")
    message.add_argument("chat", help="t.me link, @username or numeric id")
    message.add_argument("message_id", type=positive_integer, help="the exact message id")

    thread = commands.add_parser("thread", help="read replies in one topic or comment thread")
    thread.add_argument("chat", help="t.me link, @username or numeric id")
    thread.add_argument(
        "root_message_id", type=positive_integer, help="the topic or comment root"
    )
    thread.add_argument(
        "--min-id", type=cursor_integer, help="exclusive lower bound; continue here"
    )
    thread.add_argument(
        "--limit", type=limit_value, default=DEFAULT_ITEMS, help=f"1..{MAX_ITEMS}"
    )

    read = commands.add_parser("read", help="read chat history in ascending order")
    read.add_argument("chat", help="t.me link, @username or numeric id")
    read.add_argument(
        "--limit", type=limit_value, default=DEFAULT_ITEMS, help=f"1..{MAX_ITEMS}"
    )
    read.add_argument("--min-id", type=cursor_integer, help="exclusive lower bound; continue here")
    read.add_argument("--max-id", type=cursor_integer, help="exclusive upper bound")
    read.add_argument("--since", help="ISO 8601 date or datetime, inclusive")
    read.add_argument("--until", help="ISO 8601 date or datetime, inclusive")
    read.add_argument("--from-user", help="@username or numeric id of the sender")
    read.add_argument("--media-only", action="store_true", help="only messages with an attachment")
    read.add_argument(
        "--out", dest="out_path", help="write JSONL here, relative to the output root"
    )
    read.add_argument(
        "--out-limit", type=export_limit_value, default=1000, help=f"1..{EXPORT_LIMIT}"
    )

    search = commands.add_parser("search", help="search messages in one chat or globally")
    search.add_argument("query", help="text to look for")
    search.add_argument("--chat", help="restrict to one chat; omit to search every chat")
    search.add_argument(
        "--limit", type=limit_value, default=DEFAULT_ITEMS, help=f"1..{MAX_ITEMS}"
    )
    search.add_argument(
        "--max-id", type=cursor_integer, help="results page backwards; continue here"
    )
    search.add_argument(
        "--out", dest="out_path", help="write JSONL here, relative to the output root"
    )
    search.add_argument(
        "--out-limit", type=export_limit_value, default=1000, help=f"1..{EXPORT_LIMIT}"
    )

    download = commands.add_parser("download", help="download one message attachment")
    download.add_argument("chat", help="t.me link, @username or numeric id")
    download.add_argument(
        "message_id", type=positive_integer, help="the message carrying the attachment"
    )
    download.add_argument(
        "--dest-dir", dest="dest_dir", help="directory under the output root"
    )

    send = commands.add_parser("send", help="send one explicit text message or reply")
    send.add_argument("chat", help="t.me link, @username or numeric id")
    text_source = send.add_mutually_exclusive_group(required=True)
    text_source.add_argument("--text", help="the message body, as one argument")
    text_source.add_argument(
        "--text-file", help="read the body from this file under the output root"
    )
    send.add_argument(
        "--reply-to", type=positive_integer, help="id of the message being replied to"
    )

    return parser


def _plugin_version() -> str:
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    try:
        lines = pyproject.read_text(encoding="utf-8").splitlines()
    except OSError:
        return "unknown"
    for line in lines:
        if line.startswith("version = "):
            return line.partition("=")[2].strip().strip('"')
    return "unknown"


async def dispatch(arguments: argparse.Namespace, application: TelegramApplication) -> dict:
    command = arguments.command
    if command == "whoami":
        return await application.whoami()
    if command == "dialogs":
        return await application.dialogs(query=arguments.query, limit=arguments.limit)
    if command == "find-chat":
        return await application.find_chat(arguments.query, limit=arguments.limit)
    if command == "resolve":
        return await application.resolve(arguments.chat)
    if command == "message":
        return await application.message(chat=arguments.chat, message_id=arguments.message_id)
    if command == "thread":
        return await application.thread(
            chat=arguments.chat,
            root_message_id=arguments.root_message_id,
            limit=arguments.limit,
            min_id=arguments.min_id,
        )
    if command == "read":
        return await application.read(
            chat=arguments.chat,
            limit=arguments.limit,
            min_id=arguments.min_id,
            max_id=arguments.max_id,
            since=arguments.since,
            until=arguments.until,
            from_user=arguments.from_user,
            media_only=arguments.media_only,
            out_path=arguments.out_path,
            out_limit=arguments.out_limit,
        )
    if command == "search":
        return await application.search(
            query=arguments.query,
            chat=arguments.chat,
            limit=arguments.limit,
            max_id=arguments.max_id,
            out_path=arguments.out_path,
            out_limit=arguments.out_limit,
        )
    if command == "download":
        return await application.download(
            chat=arguments.chat,
            message_id=arguments.message_id,
            dest_dir=arguments.dest_dir,
        )
    if command == "send":
        application.require_send_enabled()
        text = arguments.text
        if arguments.text_file is not None:
            text = read_confined_text(
                arguments.text_file,
                root=application.config.output_root,
            )
        return await application.send(
            chat=arguments.chat,
            text=text,
            reply_to=arguments.reply_to,
        )
    raise RuntimeError(f"unsupported command: {command}")


async def _run(
    arguments: argparse.Namespace,
    environment: Mapping[str, str],
    gateway_factory: GatewayFactory | None = None,
) -> dict:
    from telegram_plugin.application import TelegramApplication
    from telegram_plugin.client import ensure_state_dir

    config = load_config_from_environment(environment)
    ensure_state_dir(config)
    gateway = (gateway_factory or _default_gateway)(config)
    try:
        return await dispatch(arguments, TelegramApplication(config, gateway))
    finally:
        await gateway.close()


def _default_gateway(config: Config) -> TelegramGateway:
    from telegram_plugin.client import TelethonGateway

    return TelethonGateway(config)


def _error(exc: Exception) -> dict:
    retry_after = getattr(exc, "seconds", None)
    is_flood_wait = isinstance(retry_after, int) and "flood" in type(exc).__name__.lower()
    if is_flood_wait:
        code = "flood_wait"
    elif isinstance(exc, TelegramPluginError):
        code = exc.code
    else:
        code = "unexpected_error"
    result: dict[str, Any] = {
        "code": code,
        "message": describe(exc),
        "retryable": is_flood_wait or bool(getattr(exc, "retryable", False)),
    }
    if is_flood_wait:
        result["retry_after_seconds"] = retry_after
    return {"error": result}


def _write_json(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def main(
    argv: Sequence[str] | None = None,
    *,
    environment: Mapping[str, str] | None = None,
    gateway_factory: GatewayFactory | None = None,
) -> int:
    try:
        arguments = build_parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)

    try:
        selected_environment = environment if environment is not None else os.environ
        result = asyncio.run(_run(arguments, selected_environment, gateway_factory))
    except Exception as exc:  # noqa: BLE001
        _write_json(_error(exc))
        return 1
    _write_json(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
