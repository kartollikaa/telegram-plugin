"""Turning Telethon objects into small, honest dictionaries."""

from __future__ import annotations

import re
from typing import Any

from telegram_plugin.refs import message_link

TEXT_LIMIT = 500
LABEL_LIMIT = 80
NAME_LIMIT = 120
MAX_ITEMS = 200
DEFAULT_ITEMS = 50

# Control characters, path separators, shell metacharacters and whitespace.
# Letters outside ASCII are left alone: they carry no meaning to a shell, and
# mangling them would rename half the world's attachments.
_UNSAFE_IN_NAME = re.compile(r"[\x00-\x1f\x7f/\\:;|&$<>*?`'\"(){}\[\]!~\s]+")


def truncate(text: str, limit: int = TEXT_LIMIT) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def label(text: str | None) -> str | None:
    """Display names and chat titles are written by other people, like message text."""
    if text is None:
        return None
    trimmed = text.strip()
    return trimmed[:LABEL_LIMIT] if len(trimmed) > LABEL_LIMIT else trimmed


def safe_name(name: str) -> str:
    """A sender picks the attachment name; it must survive being handed to a shell."""
    cleaned = _UNSAFE_IN_NAME.sub("_", name).lstrip(".-")
    if len(cleaned) > NAME_LIMIT:
        stem, dot, suffix = cleaned.rpartition(".")
        if dot and len(suffix) <= 10:
            cleaned = stem[: NAME_LIMIT - len(suffix) - 1] + "." + suffix
        else:
            cleaned = cleaned[:NAME_LIMIT]
    return cleaned or "attachment"


def render_message(
    message: Any,
    *,
    sender_name: str | None = None,
    chat_username: str | None = None,
    chat_internal_id: int | None = None,
) -> dict:
    text, was_truncated = truncate(getattr(message, "message", None) or "")
    display_name = label(sender_name)
    date = getattr(message, "date", None)
    rendered = {
        "id": message.id,
        "date": date.isoformat() if date else None,
        "sender_id": getattr(message, "sender_id", None),
        "sender_name": display_name,
        "text": text,
        "text_truncated": was_truncated,
        "link": message_link(chat_username, chat_internal_id, message.id),
    }
    media = _describe_media(getattr(message, "media", None))
    if media:
        rendered["media"] = media
    return rendered


def _describe_media(media: Any) -> dict | None:
    if media is None:
        return None
    file_name = getattr(media, "file_name", None)
    return {
        "type": getattr(media, "mime_type", None) or type(media).__name__,
        "file_name": safe_name(file_name) if file_name else None,
        "size": getattr(media, "size", None),
    }


def envelope(
    items: list[dict],
    *,
    has_more: bool,
    next_cursor: int | None,
    cursor_field: str = "min_id",
    scanned: int = 0,
    scan_truncated: bool = False,
) -> dict:
    """`note` never fabricates a remaining count — we stop early and do not know it."""
    if has_more:
        note = (
            f"{len(items)} returned, more available — continue with "
            f"{cursor_field}={next_cursor}, or pass out_path to write the whole range to a "
            "JSONL file instead of into this conversation."
        )
    elif items:
        note = f"{len(items)} returned; nothing left in this range."
    elif scanned:
        note = (
            f"nothing matched, after scanning {scanned} messages in this range — "
            "the range was not empty, the filters excluded everything in it."
        )
    else:
        note = "nothing in this range."
    if scan_truncated:
        note += (
            f" Scanning stopped at {scanned} messages to stay cheap; narrow the range with "
            "min_id or max_id and ask again."
        )
    return {
        "items": items,
        "returned": len(items),
        "has_more": has_more,
        "next_cursor": next_cursor,
        "note": note,
    }
