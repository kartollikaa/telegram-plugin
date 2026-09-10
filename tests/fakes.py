"""A stand-in for the Telethon gateway: the same protocol over in-memory rows."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from telegram_plugin.client import Batch
from telegram_plugin.errors import NoSuchMedia, NotAuthorized
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
    def __init__(self, *, authorized: bool = True, message_count: int = 12) -> None:
        self._authorized = authorized
        self.rows = [_row(i, media=i % 4 == 0) for i in range(1, message_count + 1)]
        self.sent: list[tuple[str, str]] = []

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
        for row in candidates:
            scanned += 1
            if criteria.get("media_only") and "media" not in row:
                continue
            accepted.append(row)
            if limit and len(accepted) >= limit:
                break
        bounded = bool(
            criteria.get("min_id") or criteria.get("max_id") or criteria.get("media_only")
        )
        return Batch(
            rows=accepted,
            scanned=scanned,
            total=len(self.rows),
            total_is_exact=not bounded,
        )

    async def search(self, query: str, ref: ChatRef | None, **criteria) -> Batch:
        """Newest first, like Telegram's own search, then sorted ascending."""
        self._check()
        matches = [r for r in self.rows if query in r["text"]]
        max_id = criteria.get("max_id") or 0
        if max_id:
            matches = [r for r in matches if r["id"] < max_id]
        limit = criteria.get("limit")
        page = list(reversed(matches))[:limit] if limit else list(reversed(matches))
        in_one_chat = ref is not None
        return Batch(
            rows=sorted(page, key=lambda r: r["id"]),
            scanned=len(page),
            total=len(matches) if in_one_chat else None,
            total_is_exact=in_one_chat and not max_id,
        )

    async def download(self, ref: ChatRef, message_id: int, dest: Path) -> str:
        self._check()
        row = next((r for r in self.rows if r["id"] == message_id), None)
        if row is None or "media" not in row:
            raise NoSuchMedia(message_id)
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / row["media"]["file_name"]
        target.write_bytes(b"%PDF-1.4 fake")
        return str(target)

    async def send(self, ref: ChatRef, text: str) -> dict:
        self._check()
        self.sent.append((str(ref.value), text))
        return {"id": 999, "chat_id": -1001, "chat_title": "Alpha"}
