"""Failures phrased for a model to act on, never as a traceback."""

from __future__ import annotations

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


class UnsafePath(TelegramPluginError):
    def __init__(self, candidate: str, root: str) -> None:
        super().__init__(
            f"Refusing to write to {candidate}: output must stay inside {root} and must not "
            "overwrite an existing file. Set TELEGRAM_OUTPUT_ROOT to widen the allowed area."
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
        return str(exc)
    except Exception:
        return "unprintable error"
