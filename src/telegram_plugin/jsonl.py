"""Spilling a result set to disk so it never crosses the context window."""

from __future__ import annotations

import errno
import json
import os
from collections.abc import Iterable
from pathlib import Path

from telegram_plugin.errors import UnsafePath

_EXCLUSIVE = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
_OVERWRITE = os.O_WRONLY | os.O_CREAT | os.O_TRUNC


def write_jsonl(path: str | Path, rows: Iterable[dict], *, exclusive: bool = True) -> dict:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = _open(target, exclusive)
    lines = 0
    first_id: int | None = None
    last_id: int | None = None
    with handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            lines += 1
            row_id = row.get("id")
            if first_id is None:
                first_id = row_id
            last_id = row_id
    return {"path": str(target), "lines": lines, "first_id": first_id, "last_id": last_id}


def _open(target: Path, exclusive: bool):
    """O_EXCL|O_NOFOLLOW makes "never clobber" a kernel promise, not a prior stat."""
    try:
        descriptor = os.open(target, _EXCLUSIVE if exclusive else _OVERWRITE, 0o600)
    except FileExistsError as exc:
        raise UnsafePath(str(target), str(target.parent)) from exc
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise UnsafePath(str(target), str(target.parent)) from exc
        raise
    return os.fdopen(descriptor, "w", encoding="utf-8")
