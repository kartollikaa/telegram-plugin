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
    media = _describe_media(getattr(message, "media", None))
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


def _describe_media(media: Any) -> dict | None:
    """Telethon keeps a document's metadata on `media.document` and its name in
    `document.attributes`, never on the media wrapper. Reading the wrapper — as
    this did — returns a null name and a null size for every real attachment,
    which also means `safe_name` never runs on the one string the sender picked."""
    if media is None:
        return None
    document = getattr(media, "document", None)
    file_name = _document_file_name(document)
    size = (
        getattr(media, "size", None)
        or getattr(document, "size", None)
        or _largest_photo_size(media)
    )
    mime = getattr(media, "mime_type", None) or getattr(document, "mime_type", None)
    return {
        "type": mime or type(media).__name__,
        "file_name": safe_name(file_name) if file_name else None,
        "size": size,
    }


def _document_file_name(document: Any) -> str | None:
    for attribute in getattr(document, "attributes", None) or ():
        name = getattr(attribute, "file_name", None)
        if name:
            return name
    return None


def _largest_photo_size(media: Any) -> int | None:
    """A photo has no single size; the largest rendition is the honest answer."""
    sizes = getattr(getattr(media, "photo", None), "sizes", None) or ()
    known = [value for value in (getattr(s, "size", None) for s in sizes) if isinstance(value, int)]
    return max(known) if known else None


def _flag(cursor_field: str) -> str:
    """`cursor_field` names a JSON key; the note has to name a CLI option."""
    return "--" + cursor_field.replace("_", "-")


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
) -> dict:
    """`note` states the remaining count when it is known, and says so when it is not."""
    if has_more:
        if remaining is not None:
            left = f"{remaining} more available"
        elif total is not None:
            left = f"more available (this chat holds {total} messages in total)"
        else:
            left = "more available (the count in this range is not known without scanning it)"
        note = (
            f"{len(items)} returned, {left} — continue with {_flag(cursor_field)} {next_cursor}, "
            "or pass --out PATH to write the whole range to a JSONL file instead of into this "
            "conversation."
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
            f" Scanning stopped at {scanned} messages to stay cheap; narrow the range "
            f"with {_flag(cursor_field)} and ask again."
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
