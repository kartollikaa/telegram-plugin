"""Failures phrased for a model to act on, never as a traceback."""

from __future__ import annotations

from pathlib import Path

LOGIN_HINT = "Session is not authorised — run bin/telegram-login in a terminal, then retry."


class TelegramPluginError(Exception):
    pass


class NotAuthorized(TelegramPluginError):
    def __init__(self) -> None:
        super().__init__(LOGIN_HINT)


class SessionLocked(TelegramPluginError):
    def __init__(self, path: str) -> None:
        super().__init__(
            f"The session at {path} is held by another process — Telegram revokes an auth key "
            "used by two clients at once, so this server refuses to share it. Stop the other "
            "client, or point TELEGRAM_STATE_DIR at a separate state directory with its own login."
        )


class MissingCredentials(TelegramPluginError):
    def __init__(self) -> None:
        super().__init__(
            "TELEGRAM_API_ID and TELEGRAM_API_HASH are not set — get them from "
            "https://my.telegram.org and put them in the state directory's .env "
            "(see .env.example), or export them in the host's environment."
        )


class UnsafePath(TelegramPluginError):
    def __init__(self, candidate: str, root: str) -> None:
        super().__init__(
            f"Refusing to write to {candidate}: output must stay inside the configured "
            "output directory, must not contain '..', and must not overwrite an existing file."
        )


class NotAMember(TelegramPluginError):
    def __init__(self, title: str) -> None:
        super().__init__(
            f"The invite for {title} is valid, but this account is not a member, so the history "
            "cannot be read. This plugin never joins a chat on its own — join it in a Telegram "
            "client first, then retry."
        )


class BadMoment(TelegramPluginError):
    def __init__(self, text: str) -> None:
        super().__init__(
            f"Cannot read {text!r} as a date. Use an ISO 8601 date or timestamp: 2026-01-31, "
            "2026-01-31T09:00:00Z, or 2026-01-31T09:00:00+03:00. A timestamp without a zone "
            "is read as UTC."
        )


class EscapedOutput(TelegramPluginError):
    def __init__(self, path: str) -> None:
        super().__init__(
            f"The download landed at {path}, outside the directory it was given. Nothing was "
            "returned. This should be impossible with telethon>=1.42; check which interpreter "
            "TELEGRAM_PLUGIN_PYTHON points at."
        )


class NoSuchMedia(TelegramPluginError):
    def __init__(self, message_id: int) -> None:
        super().__init__(f"Message {message_id} carries no downloadable media.")


class MediaTooLarge(TelegramPluginError):
    def __init__(self, size: int, cap: int) -> None:
        super().__init__(
            f"That attachment is {size} bytes and the ceiling is {cap}. Nothing was "
            "downloaded. Raise TELEGRAM_MAX_DOWNLOAD_BYTES only if you actually want a "
            "file that size on this disk."
        )


class SendLimitReached(TelegramPluginError):
    def __init__(self, limit: int) -> None:
        super().__init__(
            f"This server process has already sent {limit} messages, which is its ceiling. "
            "Restart the session if the operator genuinely wants to send more."
        )


def describe(exc: BaseException) -> str:
    seconds = getattr(exc, "seconds", None)
    if isinstance(seconds, int) and "flood" in type(exc).__name__.lower():
        return (
            f"Telegram asked us to wait {seconds} seconds before the next request of this kind. "
            "Do not retry immediately — retrying is what turns a short wait into a long one. "
            "Wait out the interval, or narrow the request."
        )
    if isinstance(exc, TelegramPluginError):
        return _text(exc)
    return f"{type(exc).__name__}: {_text(exc)}"


def _text(exc: BaseException) -> str:
    try:
        text = str(exc)
    except Exception:  # noqa: BLE001
        # describe() is the last line before the model; it must never raise on its own.
        return "unprintable error"
    return _without_home(text)


def _without_home(text: str) -> str:
    """Third-party errors happily quote absolute paths; the model needs the path, not the user."""
    home = str(Path.home())
    return text.replace(home, "~") if home not in ("", "/") else text
