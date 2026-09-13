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
    size = _first_known(
        getattr(media, "size", None),
        getattr(document, "size", None),
        _largest_photo_size(media),
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


def _first_known(*candidates: int | None) -> int | None:
    """`or` would read a zero-byte attachment as "size unknown"."""
    return next((value for value in candidates if isinstance(value, int)), None)


def _largest_photo_size(media: Any) -> int | None:
    """A photo has no single size; the largest rendition is the honest answer.

    The largest rendition of a modern photo is a `PhotoSizeProgressive`, which
    carries `sizes: list[int]` and no `size` at all — reading only `.size` skips
    it and under-reports by an order of magnitude.
    """
    known: list[int] = []
    for rendition in getattr(getattr(media, "photo", None), "sizes", None) or ():
        single = getattr(rendition, "size", None)
        if isinstance(single, int):
            known.append(single)
        progressive = getattr(rendition, "sizes", None) or ()
        known.extend(value for value in progressive if isinstance(value, int))
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
    """`note` states the remaining count when it is known, and says so when it is not.

    Every flag the note names carries the value to pass with it. A note that says
    "continue with --min-id" and stops is an instruction that exits 2, which is
    worse than saying nothing: the agent follows it and loses the page.
    """
    # A truncated scan means more may exist even when this page filled short of
    # its limit, and the id to resume from is known whenever anything came back.
    continuable = has_more or (scan_truncated and bool(items))
    if continuable and next_cursor is None and items:
        next_cursor = items[-1]["id"]

    if has_more:
        if remaining is not None:
            left = f"{remaining} more available"
        elif total is not None:
            left = f"more available (this chat holds {total} messages in total)"
        else:
            left = "more available (the count in this range is not known without scanning it)"
        resume = (
            f"continue with {_flag(cursor_field)} {next_cursor}"
            if next_cursor is not None
            else "ask again with a narrower range"
        )
        note = (
            f"{len(items)} returned, {left} — {resume}, or pass --out PATH to write the "
            "whole range to a JSONL file instead of into this conversation."
        )
        if scan_truncated:
            note += (
                f" The scan also stopped at {scanned} messages, so anything counted "
                "above is a floor rather than a total."
            )
    elif scan_truncated and items:
        note = (
            f"{len(items)} returned, and the scan stopped at {scanned} messages before "
            f"reaching the end of the range — more may exist. Continue with "
            f"{_flag(cursor_field)} {next_cursor}."
        )
    elif scan_truncated:
        note = (
            f"nothing matched in the first {scanned} messages, and the scan stopped "
            "there rather than reaching the end of the range — narrow it with --since "
            "or --until and ask again."
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
