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
    reply = _describe_reply(getattr(message, "reply_to", None), chat_username, chat_internal_id)
    if reply:
        rendered["reply_to"] = reply
    media = _describe_media(message)
    if media:
        rendered["media"] = media
    return rendered


def _describe_reply(header: Any, username: str | None, internal_id: int | None) -> dict | None:
    """Which message this one answers, and which topic or comment thread it sits in.

    A forum post that answers nothing still carries a header, with the topic id in
    `reply_to_msg_id`. Read as an answer, it would thread a whole chat onto its topic roots.
    """
    if header is None:
        return None
    answered = getattr(header, "reply_to_msg_id", None)
    thread = getattr(header, "reply_to_top_id", None)
    if thread is None and getattr(header, "forum_topic", False):
        answered, thread = None, answered
    if answered is None and thread is None:
        return None  # a reply to a story: there is no message to point at
    return {
        "message_id": answered,
        "link": _reply_link(header, username, internal_id, answered) if answered else None,
        "thread_id": thread,
    }


def _reply_link(
    header: Any, username: str | None, internal_id: int | None, message_id: int
) -> str | None:
    """A reply can point into another chat, where this chat's link would name a stranger."""
    peer = getattr(header, "reply_to_peer_id", None)
    if peer is None:
        return message_link(username, internal_id, message_id)
    channel_id = getattr(peer, "channel_id", None)
    return message_link(None, channel_id, message_id) if channel_id else None


def _describe_media(message: Any) -> dict | None:
    """Name, size and mime type live on the document or photo, never on the media wrapper.

    `Message.file` is Telethon's own resolver for that, and it is `None` for media that is
    not a file at all — a poll, a location, a link preview — which still gets a type.
    """
    media = getattr(message, "media", None)
    if media is None:
        return None
    file = getattr(message, "file", None)
    name = getattr(file, "name", None)
    return {
        "type": getattr(file, "mime_type", None) or type(media).__name__,
        "file_name": safe_name(name) if name else None,
        "size": getattr(file, "size", None),
    }


def envelope(
    items: list[dict],
    *,
    has_more: bool,
    next_cursor: int | None,
    cursor_field: str = "min_id",
    scanned: int = 0,
    scan_truncated: bool = False,
    remaining: int | None = None,
    total: int | None = None,
    no_cursor_hint: str | None = None,
) -> dict:
    """`note` states the remaining count when it is known, and says so when it is not."""
    if has_more:
        if remaining is not None:
            left = f"{remaining} more available"
        elif total is not None:
            left = f"more available (this chat holds {total} messages in total)"
        else:
            left = "more available (the count in this range is not known without scanning it)"
        if next_cursor is not None:
            continuation = (
                f"continue with {cursor_field}={next_cursor}, or pass out_path to write the "
                "whole range to a JSONL file instead of into this conversation."
            )
        else:
            continuation = no_cursor_hint or (
                "pass out_path to write the whole range to a JSONL file instead of into this "
                "conversation."
            )
        note = f"{len(items)} returned, {left} — {continuation}"
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
            f" Scanning stopped at {scanned} messages to stay cheap, so this is not the end of "
            "the range: continue from the cursor above, or narrow the range and ask again."
        )
    result = {
        "items": items,
        "returned": len(items),
        "has_more": has_more,
        "next_cursor": next_cursor,
        "note": note,
    }
    if remaining is not None:
        result["remaining"] = remaining
    return result
