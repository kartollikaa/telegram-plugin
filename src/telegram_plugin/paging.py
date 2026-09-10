"""Cursor pagination over ascending message ids."""

from __future__ import annotations

from typing import NamedTuple


class Page(NamedTuple):
    items: list[int]
    has_more: bool
    next_cursor: int | None


def paginate(ids_ascending: list[int], limit: int) -> Page:
    head = ids_ascending[:limit]
    has_more = len(ids_ascending) > limit
    return Page(items=head, has_more=has_more, next_cursor=head[-1] if head and has_more else None)
