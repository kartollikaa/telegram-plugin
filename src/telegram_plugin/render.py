"""Turning Telethon objects into small, honest dictionaries."""

from __future__ import annotations

from typing import Any

from telegram_plugin.refs import message_link

TEXT_LIMIT = 500
MAX_ITEMS = 200
DEFAULT_ITEMS = 50


def truncate(text: str, limit: int = TEXT_LIMIT) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def render_message(
    message: Any,
    *,
    sender_name: str | None = None,
    chat_username: str | None = None,
    chat_internal_id: int | None = None,
) -> dict:
    text, was_truncated = truncate(getattr(message, "message", None) or "")
    date = getattr(message, "date", None)
    rendered = {
        "id": message.id,
        "date": date.isoformat() if date else None,
        "sender_id": getattr(message, "sender_id", None),
        "sender_name": sender_name,
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
    return {
        "type": getattr(media, "mime_type", None) or type(media).__name__,
        "file_name": getattr(media, "file_name", None),
        "size": getattr(media, "size", None),
    }


def envelope(
    items: list[dict], *, total_seen: int, has_more: bool, next_cursor: int | None
) -> dict:
    omitted = max(total_seen - len(items), 0)
    if has_more:
        note = (
            f"{len(items)} returned, at least {omitted} more available — "
            f"continue with min_id={next_cursor}, or pass out_path to write the whole range "
            "to a JSONL file instead of into this conversation."
        )
    else:
        note = f"{len(items)} returned; nothing left in this range."
    return {
        "items": items,
        "returned": len(items),
        "has_more": has_more,
        "next_cursor": next_cursor,
        "note": note,
    }
