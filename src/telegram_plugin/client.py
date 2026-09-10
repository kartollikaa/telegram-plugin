"""The only stateful part: a Telethon client, its session lock, and state hygiene.

Deliberately absent: joining chats, marking history read, and anything else that
would be visible to other people. Resolving an invite link uses the checking
request, never the importing one.
"""

from __future__ import annotations

import asyncio
import errno
import fcntl
import os
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from telethon import TelegramClient, utils
from telethon.tl.functions.messages import CheckChatInviteRequest

from telegram_plugin.config import Config
from telegram_plugin.errors import (
    EscapedOutput,
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
    total: int | None = None
    total_is_exact: bool = False
    """`total` counts exactly the set this request ranged over.

    Telegram's own total ignores id bounds and knows nothing about filters applied here,
    so it is only a remaining count when neither was in play; otherwise it is context,
    and saying more would be inventing a number.
    """

    last_scanned_id: int | None = None
    """The id of the last message the scan looked at, accepted or not.

    Everything up to it has been examined, so it — not the last row returned — is where a
    scan that stopped at the cap must be resumed from.
    """

    cursor_supported: bool = True
    """False for a global search: message ids are only ordered within one chat."""


def _accepted(message: Any, date: Any, since: Any, media_only: bool) -> bool:
    """The filters Telegram cannot apply for us, so `history` applies them per message."""
    if since and date and date < since:
        return False
    return not (media_only and message.media is None)


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


class SessionLockHandle:
    """An acquired session lock. Release it and another client may have the account."""

    def __init__(self, descriptor: int) -> None:
        self._descriptor: int | None = descriptor

    def release(self) -> None:
        if self._descriptor is None:
            return
        try:
            fcntl.flock(self._descriptor, fcntl.LOCK_UN)
        finally:
            os.close(self._descriptor)
            self._descriptor = None


_BUSY = frozenset({errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK})


def _try_session_lock(path: str | Path) -> SessionLockHandle | None:
    """None means busy. Anything else — a filesystem without locks, an exhausted
    lock table, a lock file owned by someone else — is raised, because reporting it
    as "held by another process" sends the operator hunting a process that is not
    there."""
    lock_path = Path(f"{path}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.chmod(lock_path, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        os.close(descriptor)
        if exc.errno in _BUSY:
            return None
        raise
    except BaseException:
        os.close(descriptor)
        raise
    return SessionLockHandle(descriptor)


class _LockAttempt:
    """The waiting policy, shared so the async and sync forms cannot drift apart."""

    def __init__(self, path: str | Path, timeout: float) -> None:
        self._path = path
        self._deadline = time.monotonic() + max(timeout, 0.0)

    def try_now(self) -> SessionLockHandle | None:
        handle = _try_session_lock(self._path)
        if handle is not None:
            return handle
        if time.monotonic() >= self._deadline:
            raise SessionLocked(str(self._path))
        return None

    def delay(self) -> float:
        return min(0.1, max(self._deadline - time.monotonic(), 0.01))


async def acquire_session_lock(path: str | Path, timeout: float = 0.0) -> SessionLockHandle:
    """Wait for the account rather than refusing it outright.

    One server per session means a second session used to be told "held by another
    process" for as long as the first lived. Waiting turns that into a pause.
    """
    attempt = _LockAttempt(path, timeout)
    while (handle := attempt.try_now()) is None:
        await asyncio.sleep(attempt.delay())
    return handle


@contextmanager
def session_lock(path: str | Path, timeout: float = 0.0) -> Iterator[None]:
    """The synchronous form, for the login CLI. Two clients on one auth key make
    Telegram revoke it."""
    attempt = _LockAttempt(path, timeout)
    while (handle := attempt.try_now()) is None:
        time.sleep(attempt.delay())
    try:
        yield
    finally:
        handle.release()


class TelethonGateway:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._client: TelegramClient | None = None
        self._lock: SessionLockHandle | None = None
        self._entities: dict[tuple[str, Any], Any] = {}
        self._gate = asyncio.Lock()
        self._in_flight = 0
        self._last_used = 0.0
        self._quiet = asyncio.Event()
        self._quiet.set()
        self._idle_watcher: asyncio.Task | None = None

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[TelegramClient]:
        """Every operation runs inside this: it keeps the idle watcher honest.

        Reentrant by counting, because some operations resolve a chat first.
        """
        client = await self._connected()
        self._in_flight += 1
        self._quiet.clear()
        try:
            yield client
        finally:
            self._in_flight -= 1
            self._last_used = time.monotonic()
            if not self._in_flight:
                self._quiet.set()

    async def _connected(self) -> TelegramClient:
        async with self._gate:
            self._last_used = time.monotonic()
            if self._client is not None:
                return self._client
            config = self._config
            if not config.api_id or not config.api_hash:
                raise MissingCredentials()
            if not config.session_path.exists():
                raise NotAuthorized()

            self._lock = await acquire_session_lock(config.session_path, config.lock_wait)
            try:
                client = await self._open_client()
            except BaseException:
                # Without this the lock outlives the failure, and every later call
                # in this process is told the session is held "by another process".
                self._release_lock()
                raise
            self._client = client
            self._entities.clear()
            self._arm_idle_release()
            return client

    def _arm_idle_release(self) -> None:
        if self._config.idle_timeout <= 0:
            return
        if self._idle_watcher is not None and not self._idle_watcher.done():
            return
        self._idle_watcher = asyncio.create_task(self._release_when_idle())

    async def _release_when_idle(self) -> None:
        """Give the account back between bursts, so another session can have it.

        Sleeps to the release deadline rather than polling: the deadline is known
        exactly, and every extra wakeup is paid by every server on the machine.
        Reconnecting is cheap because the session file already holds the auth key.
        """
        timeout = self._config.idle_timeout
        while True:
            await self._quiet.wait()
            remaining = self._last_used + timeout - time.monotonic()
            if remaining > 0:
                await asyncio.sleep(remaining)
                continue
            if self._in_flight:
                continue  # a call started while we slept; wait for quiet again
            if self._client is None:
                return
            await self.close()
            return

    async def _open_client(self) -> TelegramClient:
        config = self._config
        client = TelegramClient(str(config.session_path), config.api_id, config.api_hash)
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            raise NotAuthorized()
        return client

    def _release_lock(self) -> None:
        lock, self._lock = self._lock, None
        if lock is not None:
            lock.release()

    async def _entity(self, ref: ChatRef) -> Any:
        if ref.kind == "invite":
            # Resolving an invite answers "is this account a member?" — caching it
            # would keep reporting a membership that may have changed since.
            return await self._resolve_entity(ref)
        key = (ref.kind, ref.value)
        if key in self._entities:
            return self._entities[key]
        entity = await self._resolve_entity(ref)
        self._entities[key] = entity
        return entity

    async def _resolve_entity(self, ref: ChatRef) -> Any:
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
        async with self._session() as client:
            user = await client.get_me()
            return {
                "id": user.id,
                "name": label(utils.get_display_name(user)),
                "username": getattr(user, "username", None),
            }

    async def dialogs(self, query: str | None, limit: int) -> Batch:
        async with self._session() as client:
            return await self._scan_dialogs(client, query, limit)

    async def _scan_dialogs(self, client: Any, query: str | None, limit: int) -> Batch:
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
        async with self._session():
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
        async with self._session() as client:
            return await self._walk_history(client, ref, **criteria)

    async def _walk_history(self, client: Any, ref: ChatRef, **criteria: Any) -> Batch:
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
        last_scanned_id: int | None = None
        async for message in client.iter_messages(
            entity,
            limit=None,
            min_id=criteria.get("min_id") or 0,
            max_id=criteria.get("max_id") or 0,
            from_user=criteria.get("from_user"),
            reverse=True,
        ):
            scanned += 1
            last_scanned_id = message.id
            date = getattr(message, "date", None)
            if until and date and date > until:
                break  # ascending order: nothing further can qualify
            if _accepted(message, date, since, media_only):
                rows.append(self._render(message, username, internal))
                if limit and len(rows) >= limit:
                    break
            # Outside the branch on purpose: a filter that rejects everything is exactly
            # when the cap has to bind, and checking it only after an accepted row let a
            # `since` scan walk a whole 50 000-message chat.
            if scanned >= cap:
                truncated = True
                break

        bounded = bool(
            criteria.get("min_id")
            or criteria.get("max_id")
            or since
            or until
            or media_only
            or criteria.get("from_user")
        )
        total = await self._total(client, entity) if limit and len(rows) >= limit else None
        return Batch(
            rows=rows,
            scanned=scanned,
            scan_truncated=truncated,
            last_scanned_id=last_scanned_id,
            total=total,
            total_is_exact=total is not None and not bounded,
        )

    async def _total(self, client: TelegramClient, entity: Any, **criteria: Any) -> int | None:
        """One extra round trip, and only when there is more to report."""
        try:
            probe = await client.get_messages(entity, limit=1, **criteria)
        except Exception:  # noqa: BLE001
            return None  # a count is a nicety; never fail a read for it
        return getattr(probe, "total", None)

    async def search(self, query: str, ref: ChatRef | None, **criteria: Any) -> Batch:
        """In one chat, results arrive newest first and page backwards on max_id.

        Globally they do not page by id at all: ids are per-chat, Telethon skips its own
        id range filter when there is no entity, and searchGlobal resumes on an offset
        rate and peer that no argument here can carry. So a global search keeps Telegram's
        own newest-first order and says it has no cursor rather than handing back one that
        would silently skip other chats.
        """
        async with self._session() as client:
            return await self._run_search(client, query, ref, **criteria)

    async def _run_search(
        self, client: Any, query: str, ref: ChatRef | None, **criteria: Any
    ) -> Batch:
        entity = await self._entity(ref) if ref else None
        username = getattr(entity, "username", None) if entity else None
        internal = _internal_id(entity) if entity else None
        in_one_chat = entity is not None
        max_id = (criteria.get("max_id") or 0) if in_one_chat else 0
        rows: list[dict] = []
        scanned = 0
        async for message in client.iter_messages(
            entity,
            search=query,
            limit=criteria.get("limit"),
            max_id=max_id,
        ):
            scanned += 1
            rows.append(self._render(message, username, internal))
        if in_one_chat:
            rows.sort(key=lambda row: row["id"])
        limit = criteria.get("limit")
        # Only an in-chat search reports a count worth repeating: Telegram's global total
        # is an estimate that has been observed claiming more matches for a rare word than
        # for a near-universal substring.
        total = (
            await self._total(client, entity, search=query)
            if in_one_chat and limit and len(rows) >= limit
            else None
        )
        return Batch(
            rows=rows,
            scanned=scanned,
            total=total,
            total_is_exact=total is not None and not criteria.get("max_id"),
            cursor_supported=in_one_chat,
        )

    async def download(self, ref: ChatRef, message_id: int, dest: Path) -> str:
        async with self._session() as client:
            return await self._fetch_media(client, ref, message_id, dest)

    async def _fetch_media(
        self, client: Any, ref: ChatRef, message_id: int, dest: Path
    ) -> str:
        entity = await self._entity(ref)
        messages = await client.get_messages(entity, ids=[message_id])
        message = messages[0] if messages else None
        if message is None or message.media is None:
            raise NoSuchMedia(message_id)

        # Same resolver as the rendered metadata, so the ceiling sees the size the agent
        # was shown — including a photo's, which the media wrapper does not carry.
        size = getattr(getattr(message, "file", None), "size", None)
        cap = self._config.max_download_bytes
        if isinstance(size, int) and size > cap:
            raise MediaTooLarge(size, cap)

        dest.mkdir(parents=True, exist_ok=True)
        saved = await client.download_media(message, file=str(dest))
        if saved is None:
            raise NoSuchMedia(message_id)
        return str(_with_safe_name(Path(saved), dest))

    async def send(self, ref: ChatRef, text: str) -> dict:
        async with self._session() as client:
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
        """Everything is captured before the first await.

        A call arriving while `disconnect()` is in flight must see no client, no
        lock and no watcher, so that it builds all three afresh — leaving the
        watcher in place here is how a reconnected client ended up unwatched, and
        holding the account for the life of the process again.
        """
        client, self._client = self._client, None
        lock, self._lock = self._lock, None
        watcher, self._idle_watcher = self._idle_watcher, None
        self._entities.clear()
        self._quiet.set()

        if watcher is not None and watcher is not asyncio.current_task():
            watcher.cancel()
        if client is not None:
            await client.disconnect()
        if lock is not None:
            lock.release()


def _with_safe_name(saved: Path, dest: Path) -> Path:
    """A sender chooses the attachment's name; the agent may hand the path to a shell."""
    _refuse_outside(saved, dest)
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


def _refuse_outside(saved: Path, dest: Path) -> None:
    """Telethon >= 1.42 strips a sender-supplied directory, but that must not be the only guard."""
    try:
        inside = saved.resolve().is_relative_to(dest.resolve())
    except OSError:
        inside = False
    if not inside:
        raise EscapedOutput(str(saved))


def _dialog_type(dialog: Any) -> str:
    # A supergroup is both, so `is_group` has to be asked first or every modern group
    # chat comes back labelled "channel".
    if dialog.is_group:
        return "group"
    if dialog.is_channel:
        return "channel"
    return "user"


def _entity_type(entity: Any) -> str:
    # `megagroup` is the same question `Dialog.is_group` asks, so the two tools agree.
    # A gigagroup is not one: Telegram made it act like a channel and Telethon counts it as one.
    if getattr(entity, "megagroup", False):
        return "group"
    name = type(entity).__name__.lower()
    if "channel" in name:
        return "channel"
    if "chat" in name:
        return "group"
    return "user"
