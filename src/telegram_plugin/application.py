"""Transport-independent Telegram operations shared by agent adapters."""

from __future__ import annotations

from datetime import datetime, timezone

from telegram_plugin.client import Batch, TelegramGateway
from telegram_plugin.config import Config
from telegram_plugin.errors import SendLimitReached
from telegram_plugin.jsonl import write_jsonl
from telegram_plugin.paging import paginate
from telegram_plugin.paths import safe_output_dir, safe_output_path
from telegram_plugin.refs import parse_chat_ref
from telegram_plugin.render import DEFAULT_ITEMS, envelope


class TelegramApplication:
    def __init__(self, config: Config, gateway: TelegramGateway) -> None:
        self.config = config
        self.gateway = gateway
        self._sent_so_far = 0

    async def whoami(self) -> dict:
        return await self.gateway.me()

    async def dialogs(self, query: str | None = None, limit: int = DEFAULT_ITEMS) -> dict:
        batch = await self.gateway.dialogs(query, limit)
        note = f"{len(batch.rows)} chats shown (limit {limit})."
        if batch.scan_truncated:
            note += (
                f" Scanning stopped after {batch.scanned} chats to stay cheap — narrow with query=."
            )
        elif not batch.rows and batch.scanned:
            note += f" Nothing matched among the {batch.scanned} chats scanned."
        else:
            note += " Narrow with query= if the one you want is missing."
        return {"items": batch.rows, "returned": len(batch.rows), "note": note}

    async def resolve(self, ref: str) -> dict:
        return await self.gateway.resolve(parse_chat_ref(ref))

    async def read(
        self,
        *,
        chat: str,
        limit: int = DEFAULT_ITEMS,
        min_id: int | None = None,
        max_id: int | None = None,
        since: str | None = None,
        until: str | None = None,
        from_user: str | None = None,
        media_only: bool = False,
        out_path: str | None = None,
        out_limit: int = 1000,
    ) -> dict:
        ref = parse_chat_ref(chat)
        criteria = {
            "min_id": min_id,
            "max_id": max_id,
            "since": _moment(since),
            "until": _moment(until),
            "from_user": from_user,
            "media_only": media_only,
        }
        if out_path:
            target = safe_output_path(out_path, root=self.config.output_root)
            batch = await self.gateway.history(ref, limit=out_limit, **criteria)
            return write_jsonl(target, batch.rows)

        batch = await self.gateway.history(ref, limit=limit + 1, **criteria)
        return _forward_envelope(batch, limit)

    async def search(
        self,
        *,
        query: str,
        chat: str | None = None,
        limit: int = DEFAULT_ITEMS,
        max_id: int | None = None,
        out_path: str | None = None,
        out_limit: int = 1000,
    ) -> dict:
        ref = parse_chat_ref(chat) if chat else None
        if out_path:
            target = safe_output_path(out_path, root=self.config.output_root)
            batch = await self.gateway.search(query, ref, limit=out_limit, max_id=max_id)
            return write_jsonl(target, batch.rows)

        batch = await self.gateway.search(query, ref, limit=limit + 1, max_id=max_id)
        return _backward_envelope(batch, limit)

    async def message(self, *, chat: str, message_id: int) -> dict:
        return await self.gateway.message(parse_chat_ref(chat), message_id)

    async def thread(
        self,
        *,
        chat: str,
        root_message_id: int,
        limit: int = DEFAULT_ITEMS,
    ) -> dict:
        batch = await self.gateway.thread(
            parse_chat_ref(chat),
            root_message_id,
            limit + 1,
        )
        return _thread_envelope(batch, limit)

    async def download(
        self, *, chat: str, message_id: int, dest_dir: str | None = None
    ) -> dict:
        ref = parse_chat_ref(chat)
        destination = safe_output_dir(
            dest_dir or self.config.output_root,
            root=self.config.output_root,
        )
        return {"path": await self.gateway.download(ref, message_id, destination)}

    async def send(self, *, chat: str, text: str) -> dict:
        if self._sent_so_far >= self.config.send_limit:
            raise SendLimitReached(self.config.send_limit)
        result = await self.gateway.send(parse_chat_ref(chat), text)
        self._sent_so_far += 1
        return {
            **result,
            "sent_so_far": self._sent_so_far,
            "send_limit": self.config.send_limit,
        }


def _forward_envelope(batch: Batch, limit: int) -> dict:
    page = paginate([row["id"] for row in batch.rows], limit)
    items = batch.rows[: len(page.items)]
    return envelope(
        items,
        has_more=page.has_more,
        next_cursor=page.next_cursor,
        cursor_field="min_id",
        scanned=batch.scanned,
        scan_truncated=batch.scan_truncated,
        remaining=_remaining(batch, len(items)),
        total=batch.total,
    )


def _remaining(batch: Batch, returned: int) -> int | None:
    if batch.total is None or not batch.total_is_exact:
        return None
    return max(batch.total - returned, 0)


def _backward_envelope(batch: Batch, limit: int) -> dict:
    rows = batch.rows
    has_more = len(rows) > limit
    items = rows[-limit:] if has_more else rows
    return envelope(
        items,
        has_more=has_more,
        next_cursor=items[0]["id"] if items and has_more else None,
        cursor_field="max_id",
        scanned=batch.scanned,
        remaining=_remaining(batch, len(items)),
        total=batch.total,
    )


def _thread_envelope(batch: Batch, limit: int) -> dict:
    has_more = len(batch.rows) > limit
    items = batch.rows[:limit]
    note = (
        f"{len(items)} replies returned; more exist beyond this bounded result."
        if has_more
        else f"{len(items)} replies returned; nothing left in this thread."
    )
    return {
        "items": items,
        "returned": len(items),
        "has_more": has_more,
        "next_cursor": None,
        "note": note,
    }


def _moment(text: str | None) -> datetime | None:
    if not text:
        return None
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
