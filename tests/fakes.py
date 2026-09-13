"""A stand-in for the Telethon gateway: the same protocol over in-memory rows."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from telegram_plugin.client import Batch
from telegram_plugin.errors import MessageNotFound, NoSuchMedia, NotAuthorized
from telegram_plugin.refs import ChatRef

EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _row(index: int, *, media: bool = False) -> dict:
    row = {
        "id": index,
        "date": (EPOCH + timedelta(minutes=index)).isoformat(),
        "sender_id": 100 + (index % 3),
        "sender_name": f"Sender {index % 3}",
        "text": f"message {index}",
        "text_truncated": False,
        "link": f"https://t.me/somechannel/{index}",
    }
    if media:
        row["media"] = {"type": "application/pdf", "file_name": f"{index}.pdf", "size": 1024}
    return row


class FakeGateway:
    def __init__(
        self, *, authorized: bool = True, message_count: int = 12, scan_cap: int | None = None
    ) -> None:
        self._authorized = authorized
        self._scan_cap = scan_cap
        self.rows = [_row(i, media=i % 4 == 0) for i in range(1, message_count + 1)]
        self.sent: list[tuple[str, str, int | None]] = []
        self.close_calls = 0

    def _check(self) -> None:
        if not self._authorized:
            raise NotAuthorized()

    async def me(self) -> dict:
        self._check()
        return {"id": 42, "name": "Test Account", "username": "tester", "is_bot": False}

    async def dialogs(self, query: str | None, limit: int) -> Batch:
        self._check()
        all_dialogs = [
            {"id": -1001, "title": "Alpha", "type": "channel", "username": "alpha", "unread": 0},
            {"id": -1002, "title": "Beta", "type": "group", "username": None, "unread": 3},
        ]
        needle = (query or "").casefold()
        matched = [d for d in all_dialogs if needle in d["title"].casefold()][:limit]
        return Batch(rows=matched, scanned=len(all_dialogs))

    async def resolve(self, ref: ChatRef) -> dict:
        self._check()
        return {"id": -1001, "title": "Alpha", "type": "channel", "username": "alpha"}

    async def history(self, ref: ChatRef, **criteria) -> Batch:
        """Mirrors the real gateway's contract: `limit` bounds ACCEPTED rows."""
        self._check()
        candidates = [r for r in self.rows if r["id"] > (criteria.get("min_id") or 0)]
        max_id = criteria.get("max_id") or 0
        if max_id:
            candidates = [r for r in candidates if r["id"] < max_id]
        limit = criteria.get("limit")
        accepted: list[dict] = []
        scanned = 0
        truncated = False
        last_scanned_id: int | None = None
        for row in candidates:
            scanned += 1
            last_scanned_id = row["id"]
            if not (criteria.get("media_only") and "media" not in row):
                accepted.append(row)
                if limit and len(accepted) >= limit:
                    break
            # Same shape as the real gateway: the cap binds on rejected messages too.
            if self._scan_cap and scanned >= self._scan_cap:
                truncated = True
                break
        bounded = bool(
            criteria.get("min_id") or criteria.get("max_id") or criteria.get("media_only")
        )
        return Batch(
            rows=accepted,
            scanned=scanned,
            scan_truncated=truncated,
            last_scanned_id=last_scanned_id,
            total=len(self.rows),
            total_is_exact=not bounded and not truncated,
        )

    async def search(self, query: str, ref: ChatRef | None, **criteria) -> Batch:
        """Newest first, like Telegram's own search; sorted ascending only inside one chat.

        A global search keeps Telegram's order and ignores max_id, because ids are per-chat
        and Telethon skips its own id filter when there is no entity.
        """
        self._check()
        matches = [r for r in self.rows if query in r["text"]]
        in_one_chat = ref is not None
        max_id = criteria.get("max_id") or 0
        if max_id and in_one_chat:
            matches = [r for r in matches if r["id"] < max_id]
        limit = criteria.get("limit")
        page = list(reversed(matches))[:limit] if limit else list(reversed(matches))
        return Batch(
            rows=sorted(page, key=lambda r: r["id"]) if in_one_chat else page,
            scanned=len(page),
            total=len(matches) if in_one_chat else None,
            total_is_exact=in_one_chat and not max_id,
            cursor_supported=in_one_chat,
        )

    async def message(self, ref: ChatRef, message_id: int) -> dict:
        self._check()
        row = next((row for row in self.rows if row["id"] == message_id), None)
        if row is None:
            raise MessageNotFound(message_id)
        return row

    async def thread(
        self, ref: ChatRef, root_message_id: int, limit: int, min_id: int | None = None
    ) -> Batch:
        self._check()
        matches = []
        for row in self.rows:
            reply = row.get("reply_to") or {}
            belongs = reply.get("thread_id") == root_message_id or (
                reply.get("thread_id") is None and reply.get("message_id") == root_message_id
            )
            if belongs:
                matches.append(row)
        if min_id:
            matches = [row for row in matches if row["id"] > min_id]
        rows = sorted(matches, key=lambda row: row["id"])[:limit]
        return Batch(rows=rows, scanned=len(rows))

    async def download(self, ref: ChatRef, message_id: int, dest: Path) -> str:
        self._check()
        row = next((r for r in self.rows if r["id"] == message_id), None)
        if row is None or "media" not in row:
            raise NoSuchMedia(message_id)
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / row["media"]["file_name"]
        target.write_bytes(b"%PDF-1.4 fake")
        return str(target)

    async def send(
        self,
        ref: ChatRef,
        text: str,
        reply_to: int | None = None,
    ) -> dict:
        self._check()
        self.sent.append((str(ref.value), text, reply_to))
        return {
            "message_id": 999,
            "chat_id": -1001,
            "chat_title": "Alpha",
            "reply_to": reply_to,
        }

    async def close(self) -> None:
        self.close_calls += 1
