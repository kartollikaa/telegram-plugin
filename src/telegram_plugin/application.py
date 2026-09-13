"""Transport-independent Telegram operations shared by agent adapters."""

from __future__ import annotations

from datetime import datetime, timezone

from telegram_plugin.client import DIALOG_SCAN_CAP, Batch, TelegramGateway
from telegram_plugin.config import Config
from telegram_plugin.errors import EmptyMessage, InvalidTimestamp, SendDisabled
from telegram_plugin.jsonl import write_jsonl
from telegram_plugin.matching import rank_dialogs
from telegram_plugin.paging import paginate
from telegram_plugin.paths import safe_output_dir, safe_output_path
from telegram_plugin.refs import parse_chat_ref
from telegram_plugin.render import DEFAULT_ITEMS, envelope

GLOBAL_SEARCH_HINT = (
    "a search across all chats has no id cursor — message ids are only ordered inside one "
    "chat — so narrow it with --chat, or pass --out PATH with a larger --out-limit to write "
    "the whole result to a JSONL file."
)


class TelegramApplication:
    def __init__(self, config: Config, gateway: TelegramGateway) -> None:
        self.config = config
        self.gateway = gateway

    async def whoami(self) -> dict:
        return await self.gateway.me()

    async def dialogs(self, query: str | None = None, limit: int = DEFAULT_ITEMS) -> dict:
        batch = await self.gateway.dialogs(query, limit)
        note = f"{len(batch.rows)} chats shown (limit {limit})."
        if batch.scan_truncated:
            note += (
                f" Scanning stopped after {batch.scanned} chats to stay cheap — narrow with --query."
            )
        elif not batch.rows and batch.scanned:
            note += f" Nothing matched among the {batch.scanned} chats scanned."
        else:
            note += " Narrow with --query if the one you want is missing."
        return {"items": batch.rows, "returned": len(batch.rows), "note": note}

    async def resolve(self, ref: str) -> dict:
        return await self.gateway.resolve(parse_chat_ref(ref))

    async def find_chat(self, query: str, limit: int = 10) -> dict:
        batch = await self.gateway.dialogs(None, DIALOG_SCAN_CAP)
        items = rank_dialogs(batch.rows, query, limit)
        boundary = (
            "scanning stopped at the bounded cap"
            if batch.scan_truncated
            else "the bounded dialog scan completed"
        )
        return {
            "items": items,
            "returned": len(items),
            "scanned": batch.scanned,
            "scan_truncated": batch.scan_truncated,
            "note": (
                f"{len(items)} candidate chats found from {batch.scanned} scanned; {boundary}. "
                "Compare score and matched_by, then read a small sample before choosing."
            ),
        }

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
            batch = await self.gateway.history(ref, limit=out_limit + 1, **criteria)
            rows = batch.rows[:out_limit]
            over_limit = len(batch.rows) > out_limit
            resume = rows[-1]["id"] if over_limit and rows else batch.last_scanned_id
            return _export(
                write_jsonl(target, rows),
                truncated=over_limit or batch.scan_truncated,
                scanned=batch.scanned,
                resume=f"--min-id {resume}" if resume is not None else None,
            )

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
            batch = await self.gateway.search(query, ref, limit=out_limit + 1, max_id=max_id)
            rows = batch.rows[:out_limit]
            return _export(
                write_jsonl(target, rows),
                truncated=len(batch.rows) > out_limit or batch.scan_truncated,
                scanned=batch.scanned,
                resume=None,
            )

        batch = await self.gateway.search(query, ref, limit=limit + 1, max_id=max_id)
        return _backward_envelope(
            batch, limit, ignored_max_id=bool(max_id) and not batch.cursor_supported
        )

    async def message(self, *, chat: str, message_id: int) -> dict:
        return await self.gateway.message(parse_chat_ref(chat), message_id)

    async def thread(
        self,
        *,
        chat: str,
        root_message_id: int,
        limit: int = DEFAULT_ITEMS,
        min_id: int | None = None,
    ) -> dict:
        batch = await self.gateway.thread(
            parse_chat_ref(chat),
            root_message_id,
            limit + 1,
            min_id,
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

    def require_send_enabled(self) -> None:
        if not self.config.allow_send:
            raise SendDisabled()

    async def send(
        self,
        *,
        chat: str,
        text: str,
        reply_to: int | None = None,
    ) -> dict:
        self.require_send_enabled()
        if not text.strip():
            raise EmptyMessage()
        return await self.gateway.send(parse_chat_ref(chat), text, reply_to=reply_to)


def _export(written: dict, *, truncated: bool, scanned: int, resume: str | None) -> dict:
    """An export that stopped early must not look like a complete one."""
    if not truncated:
        return {**written, "complete": True}
    continuation = f"continue with {resume} into another --out" if resume else "narrow the range"
    return {
        **written,
        "complete": False,
        "note": (
            f"this file holds a prefix of the range rather than all of it, after scanning "
            f"{scanned} messages — {continuation} and export again."
        ),
    }


def _forward_envelope(batch: Batch, limit: int) -> dict:
    """A scan that stopped at the cap has not reached the end of the range, so it says
    `has_more` and hands back the last id it *looked at* — the filters may have accepted
    nothing, leaving no returned row to continue from."""
    page = paginate([row["id"] for row in batch.rows], limit)
    items = batch.rows[: len(page.items)]
    next_cursor = page.next_cursor
    if batch.scan_truncated and next_cursor is None:
        next_cursor = batch.last_scanned_id
    return envelope(
        items,
        has_more=page.has_more or batch.scan_truncated,
        next_cursor=next_cursor,
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


def _backward_envelope(batch: Batch, limit: int, *, ignored_max_id: bool = False) -> dict:
    """A global search is not sorted by id at all — it keeps Telegram's newest-first
    order, and no id is a usable cursor across chats."""
    rows = batch.rows
    has_more = len(rows) > limit
    if not batch.cursor_supported:
        result = envelope(
            rows[:limit] if has_more else rows,
            has_more=has_more,
            next_cursor=None,
            cursor_field="max_id",
            scanned=batch.scanned,
            no_cursor_hint=GLOBAL_SEARCH_HINT,
        )
        if ignored_max_id:
            # Dropping an argument without saying so is how a caller concludes it paged.
            result["note"] += " The --max-id given was ignored: it means nothing across chats."
        return result
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
    next_cursor = items[-1]["id"] if items and has_more else None
    note = (
        f"{len(items)} replies returned, more exist — continue with --min-id "
        f"{next_cursor}."
        if has_more
        else f"{len(items)} replies returned; nothing left in this thread."
    )
    return {
        "items": items,
        "returned": len(items),
        "has_more": has_more,
        "next_cursor": next_cursor,
        "note": note,
    }


def _moment(text: str | None) -> datetime | None:
    """A typo in a date is ordinary input, not an unclassified failure: letting
    ValueError escape reported it as `unexpected_error` with a raw Python message."""
    if not text:
        return None
    normalised = f"{text[:-1]}+00:00" if text[-1] in "Zz" else text
    try:
        parsed = datetime.fromisoformat(normalised)
    except ValueError:
        raise InvalidTimestamp(text) from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
