"""Failures phrased for a model to act on, never as a traceback."""

from __future__ import annotations

from pathlib import Path

LOGIN_HINT = "Session is not authorised — run bin/telegram-login in a terminal, then retry."


class TelegramPluginError(Exception):
    code = "telegram_error"
    retryable = False


class NotAuthorized(TelegramPluginError):
    code = "not_authorized"

    def __init__(self) -> None:
        super().__init__(LOGIN_HINT)


class SessionLocked(TelegramPluginError):
    code = "session_busy"
    retryable = True

    def __init__(self, path: str) -> None:
        super().__init__(
            f"The session at {path} is held by another process — Telegram revokes an auth key "
            "used by two clients at once, so this command refuses to share it. Let the other "
            "one finish, or point TELEGRAM_STATE_DIR at a separate state directory with its "
            "own login."
        )


class MissingCredentials(TelegramPluginError):
    code = "missing_credentials"

    def __init__(self) -> None:
        super().__init__(
            "TELEGRAM_API_ID and TELEGRAM_API_HASH are not set — get them from "
            "https://my.telegram.org and put them in the state directory's .env "
            "(see .env.example), or export them in the host's environment."
        )


class UnsafePath(TelegramPluginError):
    code = "unsafe_path"

    def __init__(self, candidate: str, root: str) -> None:
        super().__init__(
            f"Refusing to write to {candidate}: output must stay inside the configured "
            "output directory, must not contain '..', and must not overwrite an existing file."
        )


class NotAMember(TelegramPluginError):
    code = "not_a_member"

    def __init__(self, title: str) -> None:
        super().__init__(
            f"The invite for {title} is valid, but this account is not a member, so the history "
            "cannot be read. This plugin never joins a chat on its own — join it in a Telegram "
            "client first, then retry."
        )


class EscapedOutput(TelegramPluginError):
    def __init__(self, path: str) -> None:
        super().__init__(
            f"The download landed at {path}, outside the directory it was given. Nothing was "
            "returned. This should be impossible with telethon>=1.42; check which interpreter "
            "TELEGRAM_PLUGIN_PYTHON points at."
        )


class NoSuchMedia(TelegramPluginError):
    code = "no_such_media"

    def __init__(self, message_id: int) -> None:
        super().__init__(f"Message {message_id} carries no downloadable media.")


class MessageNotFound(TelegramPluginError):
    code = "message_not_found"

    def __init__(self, message_id: int) -> None:
        super().__init__(f"Message {message_id} was not found in that chat.")


class MediaTooLarge(TelegramPluginError):
    code = "media_too_large"

    def __init__(self, size: int, cap: int) -> None:
        super().__init__(
            f"That attachment is {size} bytes and the ceiling is {cap}. Nothing was "
            "downloaded. Raise TELEGRAM_MAX_DOWNLOAD_BYTES only if you actually want a "
            "file that size on this disk."
        )


class SendDisabled(TelegramPluginError):
    code = "send_disabled"

    def __init__(self) -> None:
        super().__init__(
            "Sending is disabled. Set TELEGRAM_PLUGIN_ALLOW_SEND=1 in the plugin state "
            "directory's .env only when the operator wants this account to send messages."
        )


class InvalidTimestamp(TelegramPluginError):
    code = "invalid_timestamp"

    def __init__(self, value: str) -> None:
        super().__init__(
            f"Cannot read {value!r} as a time. Use ISO 8601: 2026-09-13, "
            "2026-09-13T14:30, or 2026-09-13T14:30:00+03:00."
        )


class EmptyMessage(TelegramPluginError):
    code = "empty_text"

    def __init__(self) -> None:
        super().__init__("Refusing to send an empty text message.")


class UnsafeInputPath(TelegramPluginError):
    code = "unsafe_path"

    def __init__(self, candidate: str, root: str) -> None:
        super().__init__(
            f"Refusing to read {candidate}: the text file must be a regular non-symlink file "
            f"inside the configured output directory {root}."
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
