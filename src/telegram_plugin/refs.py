"""Parsing the many ways a Telegram chat can be referred to."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from telegram_plugin.errors import TelegramPluginError

ACCEPTED = (
    "https://t.me/<name>[/<message id>], https://t.me/c/<internal id>/<message id>, "
    "https://t.me/+<invite>, @<name>, or a numeric chat id"
)

RefKind = Literal["username", "internal_id", "invite", "peer_id"]

_HOST = r"(?:https?://)?(?:www\.)?t(?:elegram)?\.me/"
_INTERNAL = re.compile(rf"^{_HOST}c/(\d+)(?:/(\d+))?/?$", re.IGNORECASE)
_INVITE = re.compile(rf"^{_HOST}(?:\+|joinchat/)([A-Za-z0-9_-]+)/?$", re.IGNORECASE)
_LINK = re.compile(rf"^{_HOST}([A-Za-z][A-Za-z0-9_]{{3,31}})(?:/(\d+))?/?$", re.IGNORECASE)
_AT = re.compile(r"^@([A-Za-z][A-Za-z0-9_]{3,31})$")
_PEER = re.compile(r"^-?\d+$")
_BARE = re.compile(r"^([A-Za-z][A-Za-z0-9_]{3,31})$")


class UnknownChatRef(TelegramPluginError, ValueError):
    def __init__(self, ref: str) -> None:
        super().__init__(f"Cannot read {ref!r} as a chat. Accepted forms: {ACCEPTED}.")


@dataclass(frozen=True)
class ChatRef:
    kind: RefKind
    value: str | int
    message_id: int | None = None


def parse_chat_ref(ref: str) -> ChatRef:
    text = (ref or "").strip()
    if not text:
        raise UnknownChatRef(ref)

    if match := _INTERNAL.match(text):
        message_id = int(match.group(2)) if match.group(2) else None
        return ChatRef("internal_id", int(match.group(1)), message_id)
    if match := _INVITE.match(text):
        return ChatRef("invite", match.group(1), None)
    if match := _LINK.match(text):
        message_id = int(match.group(2)) if match.group(2) else None
        return ChatRef("username", match.group(1), message_id)
    if match := _AT.match(text):
        return ChatRef("username", match.group(1), None)
    if _PEER.match(text):
        return ChatRef("peer_id", int(text), None)
    if match := _BARE.match(text):
        return ChatRef("username", match.group(1), None)
    raise UnknownChatRef(ref)


def message_link(username: str | None, internal_id: int | None, message_id: int) -> str | None:
    if username:
        return f"https://t.me/{username}/{message_id}"
    if internal_id is not None:
        return f"https://t.me/c/{internal_id}/{message_id}"
    return None
