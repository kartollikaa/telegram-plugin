"""The only writer to stderr. Message bodies and credentials never pass through here."""

from __future__ import annotations

import sys

from telegram_plugin.config import Config


def log(message: str) -> None:
    if not isinstance(message, str):
        raise TypeError(
            "log() takes a plain string: passing objects risks printing message bodies "
            "or credentials into the host's logs"
        )
    print(f"[telegram-plugin] {message}", file=sys.stderr)


def config_summary(config: Config) -> str:
    summary = (
        f"state dir {config.state_dir}, session {config.session_name}, "
        f"sending {'enabled' if config.allow_send else 'disabled'}"
    )
    if config.unreadable:
        # Values, not just names, would put a misconfigured secret into the host's logs.
        summary += f"; ignoring unreadable {', '.join(config.unreadable)}, using the defaults"
    return summary
