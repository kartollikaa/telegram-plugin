"""The only stateful part: a Telethon client, its session lock, and state hygiene.

Deliberately absent: joining chats, marking history read, and anything else that
would be visible to other people. Resolving an invite link uses the checking
request, never the importing one.
"""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from telethon import TelegramClient, utils
from telethon.tl.functions.messages import CheckChatInviteRequest

from telegram_plugin.config import Config
from telegram_plugin.errors import (
    MediaTooLarge,
    MissingCredentials,
    NoSuchMedia,
    NotAMember,
    NotAuthorized,
    SessionLocked,
)
from telegram_plugin.refs import ChatRef
from telegram_plugin.render import label, render_message, safe_name

SCAN_MULTIPLIER = 20
MIN_SCAN_CAP = 1000
MAX_SCAN_CAP = 20000
DIALOG_SCAN_CAP = 2000


@dataclass(frozen=True)
class Batch:
    """Rows plus what it cost to find them, so an empty result can explain itself."""

    rows: list[dict] = field(default_factory=list)
    scanned: int = 0
    scan_truncated: bool = False


def _scan_cap(limit: int | None) -> int:
    return min(max((limit or 50) * SCAN_MULTIPLIER, MIN_SCAN_CAP), MAX_SCAN_CAP)


class TelegramGateway(Protocol):
    async def me(self) -> dict: ...

    async def dialogs(self, query: str | None, limit: int) -> Batch: ...

    async def resolve(self, ref: ChatRef) -> dict: ...

    async def history(self, ref: ChatRef, **criteria: Any) -> Batch: ...

    async def search(self, query: str, ref: ChatRef | None, **criteria: Any) -> Batch: ...

    async def download(self, ref: ChatRef, message_id: int, dest: Path) -> str: ...

    async def send(self, ref: ChatRef, text: str) -> dict: ...


def ensure_state_dir(config: Config) -> None:
    config.state_dir.mkdir(parents=True, exist_ok=True)
    config.state_dir.chmod(0o700)

    # The output root may be anywhere the operator points it, including a
    # directory they use for other things — tighten only what we own.
    ours = not config.output_root.exists() or _inside(config.output_root, config.state_dir)
    config.output_root.mkdir(parents=True, exist_ok=True)
    if ours:
        config.output_root.chmod(0o700)

    for private_file in (config.dotenv_path, config.session_path):
        if private_file.exists():
            private_file.chmod(0o600)


def _inside(path: Path, parent: Path) -> bool:
    try:
        return path.resolve().is_relative_to(parent.resolve())
    except OSError:
        return False


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
        try:
            client = await self._open_client()
        except BaseException:
            # Without this the lock outlives the failure, and every later call in
            # this process is told the session is held "by another process".
            self._release_lock()
            raise
        self._client = client
        return client

    async def _open_client(self) -> TelegramClient:
        config = self._config
        client = TelegramClient(str(config.session_path), config.api_id, config.api_hash)
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            raise NotAuthorized()
        return client

    def _release_lock(self) -> None:
        if self._lock is not None:
            self._lock.__exit__(None, None, None)
            self._lock = None

    async def _entity(self, ref: ChatRef) -> Any:
        client = await self._connected()
        if ref.kind == "invite":
            invited = await client(CheckChatInviteRequest(str(ref.value)))
            chat = getattr(invited, "chat", None)
            if chat is None:
                raise NotAMember(label(getattr(invited, "title", None)) or "that chat")
            return chat
        if ref.kind == "internal_id":
            return await client.get_entity(int(f"-100{ref.value}"))
        return await client.get_entity(ref.value)

    async def me(self) -> dict:
        client = await self._connected()
        user = await client.get_me()
        return {
            "id": user.id,
            "name": label(utils.get_display_name(user)),
            "username": getattr(user, "username", None),
        }

    async def dialogs(self, query: str | None, limit: int) -> Batch:
        client = await self._connected()
        needle = (query or "").casefold()
        found: list[dict] = []
        scanned = 0
        truncated = False
        async for dialog in client.iter_dialogs():
            scanned += 1
            title = dialog.name or ""
            if needle and needle not in title.casefold():
                if scanned >= DIALOG_SCAN_CAP:
                    truncated = True
                    break
                continue
            found.append(
                {
                    "id": dialog.id,
                    "title": label(title),
                    "type": _dialog_type(dialog),
                    "username": getattr(dialog.entity, "username", None),
                    "unread": dialog.unread_count,
                }
            )
            if len(found) >= limit:
                break
            if scanned >= DIALOG_SCAN_CAP:
                truncated = True
                break
        return Batch(rows=found, scanned=scanned, scan_truncated=truncated)

    async def resolve(self, ref: ChatRef) -> dict:
        entity = await self._entity(ref)
        return {
            "id": utils.get_peer_id(entity),
            "title": label(utils.get_display_name(entity)),
            "type": _entity_type(entity),
            "username": getattr(entity, "username", None),
        }

    async def history(self, ref: ChatRef, **criteria: Any) -> Batch:
        """`limit` bounds ACCEPTED rows, never the fetched page.

        Telegram cannot filter on `since`, `until` or media presence for us, so
        those are applied here — which means the page we ask for must not be the
        page we return, or a filter would report "nothing" while the matches sat
        one page further back.
        """
        client = await self._connected()
        entity = await self._entity(ref)
        username = getattr(entity, "username", None)
        internal = _internal_id(entity)
        limit = criteria.get("limit")
        since = criteria.get("since")
        until = criteria.get("until")
        media_only = criteria.get("media_only", False)
        cap = _scan_cap(limit)

        rows: list[dict] = []
        scanned = 0
        truncated = False
        async for message in client.iter_messages(
            entity,
            limit=None,
            min_id=criteria.get("min_id") or 0,
            max_id=criteria.get("max_id") or 0,
            from_user=criteria.get("from_user"),
            reverse=True,
        ):
            scanned += 1
            date = getattr(message, "date", None)
            if until and date and date > until:
                break  # ascending order: nothing further can qualify
            if since and date and date < since:
                continue
            if media_only and message.media is None:
                continue
            rows.append(self._render(message, username, internal))
            if limit and len(rows) >= limit:
                break
            if scanned >= cap:
                truncated = True
                break
        return Batch(rows=rows, scanned=scanned, scan_truncated=truncated)

    async def search(self, query: str, ref: ChatRef | None, **criteria: Any) -> Batch:
        """Search results arrive newest first, so paging runs backwards on max_id."""
        client = await self._connected()
        entity = await self._entity(ref) if ref else None
        username = getattr(entity, "username", None) if entity else None
        internal = _internal_id(entity) if entity else None
        rows: list[dict] = []
        scanned = 0
        async for message in client.iter_messages(
            entity,
            search=query,
            limit=criteria.get("limit"),
            max_id=criteria.get("max_id") or 0,
        ):
            scanned += 1
            rows.append(self._render(message, username, internal))
        rows.sort(key=lambda row: row["id"])
        return Batch(rows=rows, scanned=scanned)

    async def download(self, ref: ChatRef, message_id: int, dest: Path) -> str:
        client = await self._connected()
        entity = await self._entity(ref)
        messages = await client.get_messages(entity, ids=[message_id])
        message = messages[0] if messages else None
        if message is None or message.media is None:
            raise NoSuchMedia(message_id)

        size = getattr(message.media, "size", None) or getattr(
            getattr(message.media, "document", None), "size", None
        )
        cap = self._config.max_download_bytes
        if isinstance(size, int) and size > cap:
            raise MediaTooLarge(size, cap)

        dest.mkdir(parents=True, exist_ok=True)
        saved = await client.download_media(message, file=str(dest))
        if saved is None:
            raise NoSuchMedia(message_id)
        return str(_with_safe_name(Path(saved)))

    async def send(self, ref: ChatRef, text: str) -> dict:
        client = await self._connected()
        entity = await self._entity(ref)
        sent = await client.send_message(entity, text)
        return {
            "id": sent.id,
            "chat_id": utils.get_peer_id(entity),
            "chat_title": label(utils.get_display_name(entity)),
        }

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
        self._release_lock()


def _with_safe_name(saved: Path) -> Path:
    """A sender chooses the attachment's name; the agent may hand the path to a shell."""
    cleaned = safe_name(saved.name)
    if cleaned == saved.name:
        return saved
    target = saved.with_name(cleaned)
    counter = 1
    while target.exists():
        target = saved.with_name(f"{Path(cleaned).stem}-{counter}{Path(cleaned).suffix}")
        counter += 1
    saved.rename(target)
    return target


def _internal_id(entity: Any) -> int | None:
    try:
        text = str(utils.get_peer_id(entity))
    except (TypeError, ValueError, AttributeError):
        return None
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
