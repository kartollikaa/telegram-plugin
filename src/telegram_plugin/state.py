"""Private local state, opened without following links."""

from __future__ import annotations

import os
import secrets
import stat
from contextlib import contextmanager
from pathlib import Path


class UnsafeState(OSError):
    def __init__(self) -> None:
        super().__init__("Unsafe plugin state: use owned regular files without links in a private directory")


@contextmanager
def private_directory(path: Path, *, create: bool = True, tighten: bool = True):
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        if os.fstat(descriptor).st_uid != os.getuid():
            raise UnsafeState()
        if tighten:
            os.fchmod(descriptor, 0o700)
        yield descriptor
    finally:
        os.close(descriptor)


def _validate_file(descriptor: int, *, tighten: bool) -> None:
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
        raise UnsafeState()
    if tighten:
        os.fchmod(descriptor, 0o600)


def open_private_file(path: Path, *, create: bool = False, tighten: bool = True) -> int:
    with private_directory(path.parent, create=create, tighten=tighten) as directory:
        flags = os.O_RDWR if create else os.O_RDONLY
        flags |= os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
        if create:
            flags |= os.O_CREAT
        descriptor = os.open(path.name, flags, 0o600, dir_fd=directory)
        try:
            _validate_file(descriptor, tighten=tighten)
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor


def tighten_private_file(path: Path) -> None:
    try:
        descriptor = open_private_file(path)
    except FileNotFoundError:
        return
    os.close(descriptor)


def read_private_text(path: Path) -> str:
    with os.fdopen(open_private_file(path, tighten=False), encoding="utf-8") as stream:
        return stream.read()


def write_private_text(path: Path, text: str) -> None:
    with private_directory(path.parent) as directory:
        temporary = f".{path.name}.{secrets.token_hex(12)}"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=directory)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(text)
            os.replace(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory)
        finally:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass


def session_files(path: Path) -> tuple[Path, ...]:
    return tuple(Path(f"{path}{suffix}") for suffix in ("", "-journal", "-wal", "-shm"))


def prepare_session_file(path: Path) -> None:
    for candidate in session_files(path):
        tighten_private_file(candidate)
    os.close(open_private_file(path, create=True))
