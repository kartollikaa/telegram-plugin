"""Spilling a result set to disk so it never crosses the context window."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path


def write_jsonl(path: str | Path, rows: Iterable[dict]) -> dict:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = 0
    first_id: int | None = None
    last_id: int | None = None
    with target.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            lines += 1
            row_id = row.get("id")
            if first_id is None:
                first_id = row_id
            last_id = row_id
    return {"path": str(target), "lines": lines, "first_id": first_id, "last_id": last_id}
