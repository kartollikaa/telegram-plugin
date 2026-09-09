"""A stand-in for the Telethon gateway: the same protocol over in-memory rows."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

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

    async def dialogs(self, query: str | None, limit: int) -> list[dict]:
        self._check()
        all_dialogs = [
            {"id": -1001, "title": "Alpha", "type": "channel", "username": "alpha", "unread": 0},
            {"id": -1002, "title": "Beta", "type": "group", "username": None, "unread": 3},
        ]
        needle = (query or "").casefold()
        return [d for d in all_dialogs if needle in d["title"].casefold()][:limit]

    async def resolve(self, ref: ChatRef) -> dict:
        self._check()
        return {"id": -1001, "title": "Alpha", "type": "channel", "username": "alpha"}

    async def history(self, ref: ChatRef, **criteria) -> list[dict]:
        self._check()
        rows = self.rows
        if criteria.get("media_only"):
            rows = [r for r in rows if "media" in r]
        min_id = criteria.get("min_id") or 0
        rows = [r for r in rows if r["id"] > min_id]
        max_id = criteria.get("max_id") or 0
        if max_id:
            rows = [r for r in rows if r["id"] < max_id]
        limit = criteria.get("limit")
        return rows[:limit] if limit else rows

    async def search(self, query: str, ref: ChatRef | None, limit: int) -> list[dict]:
        self._check()
        return [r for r in self.rows if query in r["text"]][:limit]

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
        return {"id": 999, "chat_id": -1001}
