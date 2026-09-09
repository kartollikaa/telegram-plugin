"""Interactive login, deliberately out of process.

Nothing here is reachable through a tool call, and no secret is accepted as a
command-line argument, where it would land in shell history and the process table.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from collections.abc import Mapping

from telethon import TelegramClient, utils

from telegram_plugin.client import ensure_state_dir, session_lock
from telegram_plugin.config import Config, load_config_from_environment
from telegram_plugin.errors import MissingCredentials, SessionLocked, describe


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="telegram-login",
        description=(
            "Authorise this plugin's own Telegram session. You will be asked for a phone "
            "number, the code Telegram sends you, and a two-factor password if the account "
            "has one. Answers are typed at the prompt, never passed as arguments."
        ),
    )
    parser.add_argument(
        "--state-dir",
        help="Where to keep the session (default: $TELEGRAM_STATE_DIR or ~/.local/state/telegram-plugin)",
    )
    return parser.parse_args(argv)


def _config_for(argv: argparse.Namespace, env: Mapping[str, str]) -> Config:
    if argv.state_dir:
        merged = dict(env)
        merged["TELEGRAM_STATE_DIR"] = argv.state_dir
        return load_config_from_environment(merged)
    return load_config_from_environment(env)


async def _authorise(config: Config) -> int:
    if not config.api_id or not config.api_hash:
        raise MissingCredentials()

    client = TelegramClient(str(config.session_path), config.api_id, config.api_hash)
    await client.start(
        phone=lambda: input("Phone number, with country code: "),
        code_callback=lambda: input("Login code Telegram just sent you: "),
        password=lambda: getpass.getpass("Two-factor password (input hidden): "),
    )
    try:
        me = await client.get_me()
        if config.session_path.exists():
            config.session_path.chmod(0o600)
        print(f"Authorised as {utils.get_display_name(me)} (id {me.id}).")
        print(f"Session stored at {config.session_path}")
        return 0
    finally:
        await client.disconnect()


def main(argv: list[str] | None = None) -> int:
    arguments = _parse(sys.argv[1:] if argv is None else argv)
    config = _config_for(arguments, os.environ)
    ensure_state_dir(config)
    try:
        # Taken before anything else: if the server holds this session, logging in
        # from here would put two clients on one auth key and Telegram would revoke it.
        with session_lock(config.session_path):
            return asyncio.run(_authorise(config))
    except SessionLocked as exc:
        print(describe(exc), file=sys.stderr)
        return 1
    except MissingCredentials as exc:
        print(describe(exc), file=sys.stderr)
        return 2
    except (EOFError, KeyboardInterrupt):
        print("Cancelled — no session was written.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
