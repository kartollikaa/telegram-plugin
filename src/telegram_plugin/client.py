"""The only stateful part: a Telethon client, its session lock, and state directory hygiene.

Deliberately absent: joining chats, marking history read, and anything else that
would be visible to other people. Resolving an invite link uses the checking
request, never the importing one.
"""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Protocol

from telethon import TelegramClient, utils
from telethon.tl.functions.messages import CheckChatInviteRequest

from telegram_plugin.config import Config
from telegram_plugin.errors import (
    MissingCredentials,
    NoSuchMedia,
    NotAMember,
    NotAuthorized,
    SessionLocked,
)
from telegram_plugin.refs import ChatRef
from telegram_plugin.render import render_message


class TelegramGateway(Protocol):
    async def me(self) -> dict: ...

    async def dialogs(self, query: str | None, limit: int) -> list[dict]: ...

    async def resolve(self, ref: ChatRef) -> dict: ...

    async def history(self, ref: ChatRef, **criteria: Any) -> list[dict]: ...

    async def search(self, query: str, ref: ChatRef | None, limit: int) -> list[dict]: ...

    async def download(self, ref: ChatRef, message_id: int, dest: Path) -> str: ...

    async def send(self, ref: ChatRef, text: str) -> dict: ...


def ensure_state_dir(config: Config) -> None:
    config.state_dir.mkdir(parents=True, exist_ok=True)
    config.state_dir.chmod(0o700)
    config.output_root.mkdir(parents=True, exist_ok=True)
    config.output_root.chmod(0o700)
    if config.dotenv_path.exists():
        config.dotenv_path.chmod(0o600)


@contextmanager
def session_lock(path: str | Path) -> Iterator[None]:
    """Exclusive, non-blocking. Two clients on one auth key make Telegram revoke it."""
    lock_path = Path(f"{path}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    os.chmod(lock_path, 0o600)
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise SessionLocked(str(path)) from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
    finally:
        os.close(handle)


class TelethonGateway:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._client: TelegramClient | None = None
        self._lock: Any = None

    async def _connected(self) -> TelegramClient:
        if self._client is not None:
            return self._client
        config = self._config
        if not config.api_id or not config.api_hash:
            raise MissingCredentials()
        if not config.session_path.exists():
            raise NotAuthorized()
        self._lock = session_lock(config.session_path)
        self._lock.__enter__()
        client = TelegramClient(str(config.session_path), config.api_id, config.api_hash)
        await client.connect()
        if not await client.is_user_authorized():
            raise NotAuthorized()
        self._client = client
        return client

    async def _entity(self, ref: ChatRef) -> Any:
        client = await self._connected()
        if ref.kind == "invite":
            invited = await client(CheckChatInviteRequest(str(ref.value)))
            chat = getattr(invited, "chat", None)
            if chat is None:
                raise NotAMember(getattr(invited, "title", None) or "that chat")
            return chat
        if ref.kind == "internal_id":
            return await client.get_entity(utils.get_peer(int(f"-100{ref.value}")))
        return await client.get_entity(ref.value)

    async def me(self) -> dict:
        client = await self._connected()
        user = await client.get_me()
        return {
            "id": user.id,
            "name": utils.get_display_name(user),
            "username": getattr(user, "username", None),
            "is_bot": bool(getattr(user, "bot", False)),
        }

    async def dialogs(self, query: str | None, limit: int) -> list[dict]:
        client = await self._connected()
        needle = (query or "").casefold()
        found: list[dict] = []
        async for dialog in client.iter_dialogs():
            title = dialog.name or ""
            if needle and needle not in title.casefold():
                continue
            found.append(
                {
                    "id": dialog.id,
                    "title": title,
                    "type": _dialog_type(dialog),
                    "username": getattr(dialog.entity, "username", None),
                    "unread": dialog.unread_count,
                }
            )
            if len(found) >= limit:
                break
        return found

    async def resolve(self, ref: ChatRef) -> dict:
        entity = await self._entity(ref)
        return {
            "id": utils.get_peer_id(entity),
            "title": utils.get_display_name(entity),
            "type": _entity_type(entity),
            "username": getattr(entity, "username", None),
        }

    async def history(self, ref: ChatRef, **criteria: Any) -> list[dict]:
        client = await self._connected()
        entity = await self._entity(ref)
        username = getattr(entity, "username", None)
        internal = _internal_id(entity)
        since = criteria.get("since")
        until = criteria.get("until")
        media_only = criteria.get("media_only", False)
        rows: list[dict] = []
        async for message in client.iter_messages(
            entity,
            limit=criteria.get("limit"),
            min_id=criteria.get("min_id") or 0,
            max_id=criteria.get("max_id") or 0,
            from_user=criteria.get("from_user"),
            reverse=True,
        ):
            if media_only and message.media is None:
                continue
            if since and message.date and message.date < since:
                continue
            if until and message.date and message.date > until:
                continue
            rows.append(self._render(message, username, internal))
        return rows

    async def search(self, query: str, ref: ChatRef | None, limit: int) -> list[dict]:
        client = await self._connected()
        entity = await self._entity(ref) if ref else None
        username = getattr(entity, "username", None) if entity else None
        internal = _internal_id(entity) if entity else None
        rows: list[dict] = []
        async for message in client.iter_messages(entity, search=query, limit=limit):
            rows.append(self._render(message, username, internal))
        rows.sort(key=lambda row: row["id"])
        return rows

    async def download(self, ref: ChatRef, message_id: int, dest: Path) -> str:
        client = await self._connected()
        entity = await self._entity(ref)
        messages = await client.get_messages(entity, ids=[message_id])
        message = messages[0] if messages else None
        if message is None or message.media is None:
            raise NoSuchMedia(message_id)
        dest.mkdir(parents=True, exist_ok=True)
        saved = await client.download_media(message, file=str(dest))
        if saved is None:
            raise NoSuchMedia(message_id)
        return str(saved)

    async def send(self, ref: ChatRef, text: str) -> dict:
        client = await self._connected()
        entity = await self._entity(ref)
        sent = await client.send_message(entity, text)
        return {"id": sent.id, "chat_id": utils.get_peer_id(entity)}

    def _render(self, message: Any, username: str | None, internal: int | None) -> dict:
        sender = getattr(message, "sender", None)
        name = utils.get_display_name(sender) if sender else None
        return render_message(
            message, sender_name=name, chat_username=username, chat_internal_id=internal
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None
        if self._lock is not None:
            self._lock.__exit__(None, None, None)
            self._lock = None


def _internal_id(entity: Any) -> int | None:
    peer_id = utils.get_peer_id(entity)
    text = str(peer_id)
    return int(text[4:]) if text.startswith("-100") else None


def _dialog_type(dialog: Any) -> str:
    if dialog.is_channel:
        return "channel"
    if dialog.is_group:
        return "group"
    return "user"


def _entity_type(entity: Any) -> str:
    name = type(entity).__name__.lower()
    if "channel" in name:
        return "channel"
    if "chat" in name:
        return "group"
    return "user"
