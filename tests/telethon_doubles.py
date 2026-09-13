"""Message doubles built from real Telethon types.

The lesson this file exists for: a double that puts `file_name`, `mime_type` and `size`
flat on the media wrapper is not a simplification of Telethon, it is a different contract
— and it hid the fact that every rendered media block came back empty. Anything standing
in for a message here carries the real `MessageMediaX` object and resolves `.file` the
way `telethon.tl.custom.Message` does.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from telethon.tl.custom.file import File
from telethon.tl.types import (
    Document,
    DocumentAttributeFilename,
    MessageMediaDocument,
    MessageMediaGeo,
    MessageMediaPhoto,
    Photo,
    PhotoSize,
)

EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


class FakeMessage:
    """`file` resolves off the document or photo, exactly as `Message.file` does."""

    def __init__(self, *, id: int, date: datetime, message: str = "", sender_id: int = 1,
                 media: Any = None, source: Any = None, sender: Any = None,
                 reply_to: Any = None) -> None:
        self.id = id
        self.date = date
        self.message = message
        self.sender_id = sender_id
        self.media = media
        self.sender = sender
        self.reply_to = reply_to
        self._source = source

    @property
    def file(self) -> File | None:
        return File(self._source) if self._source is not None else None


def document(name: str = "report.pdf", *, mime: str = "application/pdf", size: int = 1024):
    return Document(
        id=1,
        access_hash=2,
        file_reference=b"",
        date=EPOCH,
        mime_type=mime,
        size=size,
        dc_id=2,
        attributes=[DocumentAttributeFilename(name)],
    )


def photo(size: int = 9876):
    return Photo(
        id=1,
        access_hash=2,
        file_reference=b"",
        date=EPOCH,
        sizes=[PhotoSize(type="x", w=100, h=100, size=size)],
        dc_id=2,
    )


def document_message(index: int, *, name: str = "report.pdf", size: int = 1024, **extra):
    source = document(name, size=size)
    return FakeMessage(id=index, media=MessageMediaDocument(document=source), source=source,
                       **{"date": EPOCH, **extra})


def photo_message(index: int, *, size: int = 9876, **extra):
    source = photo(size)
    return FakeMessage(id=index, media=MessageMediaPhoto(photo=source), source=source,
                       **{"date": EPOCH, **extra})


def geo_message(index: int, **extra):
    """Media that is not a file at all: `Message.file` is None and only a type survives."""
    return FakeMessage(id=index, media=MessageMediaGeo(geo=None), source=None,
                       **{"date": EPOCH, **extra})


def plain_message(index: int, **extra):
    return FakeMessage(id=index, **{"date": EPOCH, **extra})
