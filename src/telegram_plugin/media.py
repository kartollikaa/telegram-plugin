"""Content-based selection for Telegram's JPEG/PNG photo transport."""


def photo_format(stream) -> str | None:
    position = stream.tell()
    try:
        header = stream.read(16)
    finally:
        stream.seek(position)
    if header.startswith(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"):
        return "png"
    if header.startswith(b"\xff\xd8\xff"):
        return "jpg"
    return None
