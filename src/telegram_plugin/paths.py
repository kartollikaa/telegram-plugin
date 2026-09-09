"""Output paths are confined: a tool call must not be able to write anywhere on disk."""

from __future__ import annotations

from pathlib import Path

from telegram_plugin.errors import UnsafePath


def safe_output_path(
    candidate: str | Path, *, root: Path, must_not_exist: bool = True
) -> Path:
    root_resolved = Path(root).resolve()
    raw = Path(candidate)
    target = raw if raw.is_absolute() else root_resolved / raw
    resolved = _resolve_existing_ancestors(target)

    if resolved == root_resolved or root_resolved not in resolved.parents:
        raise UnsafePath(str(candidate), str(root_resolved))
    if must_not_exist and resolved.exists():
        raise UnsafePath(str(candidate), str(root_resolved))
    return resolved


def _resolve_existing_ancestors(target: Path) -> Path:
    """Resolve symlinks in the part that exists, keep the rest literal."""
    existing = target
    tail: list[str] = []
    while not existing.exists() and existing != existing.parent:
        tail.append(existing.name)
        existing = existing.parent
    resolved = existing.resolve()
    for name in reversed(tail):
        resolved = resolved / name
    return resolved
