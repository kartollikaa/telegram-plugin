"""Authorisation, deliberately out of process.

Three ways in, none of which lets a secret through a tool call:

* `--status` — reports whether the session is usable, and under whom.
* `--qr` — publishes a `tg://login` link, waits for it to be confirmed in a
  Telegram client, and writes the session. Nothing is typed, so this is the path
  a plugin command can drive end to end.
* no flags — the classic phone, code and two-factor prompts, for a human at a
  terminal. It is the fallback when an account has a second factor, which the QR
  flow cannot satisfy on its own.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from telethon import TelegramClient, utils
from telethon.errors import SessionPasswordNeededError

from telegram_plugin.client import ensure_state_dir, session_lock
from telegram_plugin.config import Config, load_config_from_environment
from telegram_plugin.errors import MissingCredentials, SessionLocked, describe

DEFAULT_QR_TIMEOUT = 60.0
DEFAULT_QR_ATTEMPTS = 5
TERMINAL_HINT = "run bin/telegram-login in your own terminal to finish with the password"


def status_payload(config: Config, *, authorized: bool, account: dict | None) -> dict:
    """What the login state looks like from outside. Never carries a credential."""
    has_credentials = bool(config.api_id and config.api_hash)
    payload: dict[str, Any] = {
        "credentials": "present" if has_credentials else "missing",
        "session_file": "present" if config.session_path.exists() else "missing",
        "authorized": authorized,
        "state_dir": str(config.state_dir),
    }
    if account:
        payload["account"] = account
    if not has_credentials:
        payload["hint"] = (
            "TELEGRAM_API_ID and TELEGRAM_API_HASH are not set. Create an application at "
            f"https://my.telegram.org and put both into {config.dotenv_path}"
        )
    elif not authorized:
        payload["hint"] = "not authorised yet — run the login with --qr, or without flags"
    return payload


def write_status(config: Config, payload: dict) -> None:
    path = config.auth_status_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


async def qr_login(
    config: Config,
    client: Any,
    *,
    timeout: float = DEFAULT_QR_TIMEOUT,
    attempts: int = DEFAULT_QR_ATTEMPTS,
) -> dict:
    """Publish a login link, then wait for a Telegram client to confirm it.

    The link is written to the status file *before* the wait starts: `wait()` only
    resolves while it is running, so whoever is driving this needs the link in
    hand while we sit here.
    """
    try:
        await client.connect()
        if await client.is_user_authorized():
            return _authorized(config, await _describe_me(client))

        qr = await client.qr_login()
        for attempt in range(1, attempts + 1):
            write_status(
                config,
                {
                    "state": "waiting",
                    "url": qr.url,
                    "attempt": attempt,
                    "attempts": attempts,
                    "instruction": (
                        "Open this link on a device already signed in to Telegram, or scan it "
                        "as a QR code from Settings -> Devices -> Link Desktop Device."
                    ),
                },
            )
            try:
                await qr.wait(timeout)
            except asyncio.TimeoutError:
                if attempt < attempts:
                    # recreate() always re-requests, so only ask when the token is
                    # actually spent — this is a login endpoint, not a cheap one.
                    if _expired(qr):
                        await qr.recreate()
                    continue
                return _finish(config, {
                    "state": "expired",
                    "hint": "the link was never confirmed; start the login again",
                })
            except SessionPasswordNeededError:
                return _finish(config, {
                    "state": "needs_password",
                    "hint": (
                        "this account has a two-factor password, which cannot be typed "
                        f"through a tool call — {TERMINAL_HINT}"
                    ),
                })
            return _authorized(config, await _describe_me(client))
        return _finish(config, {"state": "expired", "hint": "no attempts left"})
    finally:
        await client.disconnect()


def _authorized(config: Config, account: dict) -> dict:
    _tighten(config.session_path)
    return _finish(config, {"state": "authorized", "account": account})


def _finish(config: Config, payload: dict) -> dict:
    write_status(config, payload)
    return payload


async def _describe_me(client: Any) -> dict:
    me = await client.get_me()
    return {"id": me.id, "name": utils.get_display_name(me)}


def _expired(qr: Any) -> bool:
    expires = getattr(qr, "expires", None)
    if not isinstance(expires, datetime):
        return True  # unknown expiry: re-request rather than wait on a dead token
    reference = datetime.now(expires.tzinfo or timezone.utc)
    return expires <= reference


def _tighten(path: Path) -> None:
    if path.exists():
        path.chmod(0o600)


async def _interactive(config: Config) -> int:
    client = TelegramClient(str(config.session_path), config.api_id, config.api_hash)
    try:
        await client.connect()
        _tighten(config.session_path)
        await client.start(
            phone=lambda: input("Phone number, with country code: "),
            code_callback=lambda: input("Login code Telegram just sent you: "),
            password=lambda: getpass.getpass("Two-factor password (input hidden): "),
        )
        account = await _describe_me(client)
        _finish(config, {"state": "authorized", "account": account})
        print(f"Authorised as {account['name']} (id {account['id']}).")
        print(f"Session stored at {config.session_path}")
        return 0
    finally:
        _tighten(config.session_path)
        await client.disconnect()


async def _report_status(config: Config) -> int:
    if not (config.api_id and config.api_hash) or not config.session_path.exists():
        print(json.dumps(status_payload(config, authorized=False, account=None), indent=2))
        return 0
    client = TelegramClient(str(config.session_path), config.api_id, config.api_hash)
    try:
        await client.connect()
        authorized = await client.is_user_authorized()
        account = await _describe_me(client) if authorized else None
    finally:
        await client.disconnect()
    print(json.dumps(status_payload(config, authorized=authorized, account=account), indent=2))
    return 0


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="telegram-login",
        description=(
            "Authorise this plugin's own Telegram session. With --qr you confirm a link in a "
            "Telegram client and nothing is typed. Without flags you are asked for a phone "
            "number, the code Telegram sends, and a two-factor password if the account has "
            "one — always at the prompt, never as an argument."
        ),
    )
    parser.add_argument("--status", action="store_true", help="report the login state and exit")
    parser.add_argument("--qr", action="store_true", help="log in by confirming a link")
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_QR_TIMEOUT,
        help=f"seconds to wait for each link (default {DEFAULT_QR_TIMEOUT:.0f})",
    )
    parser.add_argument("--state-dir", help="override the state directory")
    return parser.parse_args(argv)


def _status_command(config: Config) -> int:
    """A running server holding the session is itself an answer, not a failure."""
    try:
        with session_lock(config.session_path):
            return asyncio.run(_report_status(config))
    except SessionLocked:
        payload = status_payload(config, authorized=False, account=None)
        payload["authorized"] = "unknown"
        payload["session_in_use"] = True
        payload["hint"] = (
            "another process holds this session — almost certainly the plugin's own MCP "
            "server, which means a session exists. Stop that client to re-check whether it "
            "is still authorised."
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0


def main(argv: list[str] | None = None) -> int:
    arguments = _parse(sys.argv[1:] if argv is None else argv)
    environment = dict(os.environ)
    if arguments.state_dir:
        environment["TELEGRAM_STATE_DIR"] = arguments.state_dir
    config = load_config_from_environment(environment)
    ensure_state_dir(config)

    if arguments.status:
        return _status_command(config)

    try:
        # Taken before anything else, including the credentials check: if the
        # server holds this session, logging in here would put two clients on one
        # auth key and Telegram would revoke it.
        with session_lock(config.session_path):
            if not (config.api_id and config.api_hash):
                raise MissingCredentials()
            if arguments.qr:
                client = TelegramClient(str(config.session_path), config.api_id, config.api_hash)
                result = asyncio.run(qr_login(config, client, timeout=arguments.timeout))
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0 if result["state"] == "authorized" else 1
            return asyncio.run(_interactive(config))
    except (SessionLocked, MissingCredentials) as exc:
        print(describe(exc), file=sys.stderr)
        return 1 if isinstance(exc, SessionLocked) else 2
    except (EOFError, KeyboardInterrupt):
        print("Cancelled — no session was written.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
