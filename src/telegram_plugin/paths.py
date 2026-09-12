"""Output paths are confined: a tool call must not be able to write anywhere on disk.

The subtle part is `..`. Resolving only the ancestors that already exist leaves a
literal `..` in the tail, and `Path.parents` does not normalise it — so a payload
like `x/../../etc/passwd` used to look like a child of the root. `..` is now
refused outright, and the result is normalised and checked again.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from telegram_plugin.errors import UnsafeInputPath, UnsafePath


def safe_output_path(candidate: str | Path, *, root: Path, must_not_exist: bool = True) -> Path:
    resolved, root_resolved = _confine(candidate, root, allow_root=False)
    if must_not_exist and (resolved.exists() or resolved.is_symlink()):
        raise UnsafePath(str(candidate), str(root_resolved))
    return resolved


def safe_output_dir(candidate: str | Path, *, root: Path) -> Path:
    """Same confinement, but a directory may legitimately be the root itself."""
    resolved, _ = _confine(candidate, root, allow_root=True)
    return resolved


def read_confined_text(candidate: str | Path, *, root: Path) -> str:
    text = str(candidate)
    root_resolved = Path(root).resolve()
    raw = Path(candidate)
    if "\x00" in text or ".." in raw.parts:
        raise UnsafeInputPath(text, str(root_resolved))
    if raw.is_absolute():
        try:
            relative = raw.relative_to(root_resolved)
        except ValueError:
            raise UnsafeInputPath(text, str(root_resolved)) from None
    else:
        relative = raw
    if not relative.parts:
        raise UnsafeInputPath(text, str(root_resolved))

    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    file_flags = os.O_RDONLY | os.O_NOFOLLOW
    directory_fds: list[int] = []
    try:
        directory_fds.append(os.open(root_resolved, directory_flags))
        for component in relative.parts[:-1]:
            directory_fds.append(
                os.open(
                    component,
                    directory_flags,
                    dir_fd=directory_fds[-1],
                )
            )
        file_fd = os.open(
            relative.parts[-1],
            file_flags,
            dir_fd=directory_fds[-1],
        )
        try:
            if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                raise UnsafeInputPath(text, str(root_resolved))
            with os.fdopen(file_fd, "rb") as stream:
                file_fd = -1
                return stream.read().decode("utf-8")
        finally:
            if file_fd >= 0:
                os.close(file_fd)
    except (OSError, UnicodeError):
        raise UnsafeInputPath(text, str(root_resolved)) from None
    finally:
        for directory_fd in reversed(directory_fds):
            os.close(directory_fd)


def _confine(candidate: str | Path, root: Path, *, allow_root: bool) -> tuple[Path, Path]:
    text = str(candidate)
    root_resolved = Path(root).resolve()
    raw = Path(text)

    if "\x00" in text or ".." in raw.parts:
        raise UnsafePath(text, str(root_resolved))

    target = raw if raw.is_absolute() else root_resolved / raw
    resolved = Path(os.path.normpath(_resolve_existing_ancestors(target)))

    if resolved == root_resolved:
        if allow_root:
            return resolved, root_resolved
        raise UnsafePath(text, str(root_resolved))
    if not resolved.is_relative_to(root_resolved):
        raise UnsafePath(text, str(root_resolved))
    return resolved, root_resolved


def _resolve_existing_ancestors(target: Path) -> Path:
    """Resolve symlinks in the part that exists, keep the rest literal."""
    existing = target
    tail: list[str] = []
    while not _exists(existing) and existing != existing.parent:
        tail.append(existing.name)
        existing = existing.parent
    resolved = existing.resolve()
    for name in reversed(tail):
        resolved = resolved / name
    return resolved


def _exists(path: Path) -> bool:
    try:
        return path.exists()
    except (OSError, ValueError):
        return False
