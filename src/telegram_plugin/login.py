"""Authorisation, deliberately out of process and in front of a human.

* no flags — choose phone and code or an inline QR, with a hidden two-step password.
* `--logout` — confirm and revoke this plugin's session.
* `--status` — whether the session is usable, and under whom. Changes nothing.
* `--wait` — blocks until a login running elsewhere reports its outcome, so an agent
  can hand the operator the command and learn when they are done.

Every way out of the login leaves its outcome in `auth-status.json`.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import fcntl
import json
import math
import os
import signal
import sqlite3
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from telethon import TelegramClient, utils
from telethon.errors import (
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeEmptyError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberBannedError,
    PhoneNumberInvalidError,
    PhoneNumberUnoccupiedError,
    RPCError,
    SessionPasswordNeededError,
)
from telethon.tl.functions.account import GetPasswordRequest
from telethon.tl.types.auth import SentCodeTypeApp, SentCodeTypeCall, SentCodeTypeSms

from telegram_plugin.client import ensure_state_dir, session_lock
from telegram_plugin.config import Config, load_config_from_environment
from telegram_plugin.errors import MissingCredentials, SessionLocked, describe
from telegram_plugin.privacy import private_json, private_output, redact_text, redact_value
from telegram_plugin.state import (
    open_private_file,
    prepare_session_file,
    read_private_text,
    tighten_private_file,
    write_private_text,
)

FINAL_STATES = frozenset({
    "authorized", "logged_out", "needs_password", "cancelled", "failed", "expired",
})
DEFAULT_WAIT = 480.0
COMMAND = "bin/telegram-login"

NEEDS_TERMINAL = (
    f"{COMMAND} is interactive and needs a terminal for login choices, hidden passwords "
    f"and logout confirmation. Run {COMMAND} --browser to open a private local form "
    "automatically when the host has no interactive input."
)
QR_REMOVED = (
    f"--qr was replaced: run {COMMAND} --method qr for QR frames in command output, "
    f"or run {COMMAND} without flags for the interactive menu."
)


@dataclass(frozen=True)
class Pace:
    """How the QR screen spends time. Telegram issues login tokens for about 30 seconds."""

    rotate_after: float = 0.5
    """Ask for the next token this long after the current one expires. Telegram hands back
    the same token until then (seen live), so asking earlier cannot shorten the gap."""
    retry: float = 1.0
    max_retry: float = 5.0
    """Backoff when the same token comes back anyway: this clock runs ahead of Telegram's."""
    min_show: float = 10.0
    """Floor on a fresh token's screen time, for the same clock skew: every token would look
    expired on arrival and the loop would turn into a request storm."""
    max_show: float = 60.0
    limit: float = 300.0
    """Give up on the QR after this long without a scan; the operator reruns the command."""
    grace: float = 10.0
    """At the limit, how long a scan already being confirmed may still take to land."""
    tick: float = 0.1
    attempts: int = 3


DEFAULT_PACE = Pace()


class LoginFailed(Exception):
    def __init__(self, hint: str) -> None:
        super().__init__(hint)
        self.hint = hint


class Interrupted(KeyboardInterrupt):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class Ui(Protocol):
    def say(self, text: str = "") -> None: ...

    def ask(self, prompt: str) -> str: ...

    def ask_secret(self, prompt: str) -> str: ...

    def enter_qr(self) -> None: ...

    def show_qr(self, url: str, seconds: int) -> None: ...

    def countdown(self, seconds: int) -> None: ...

    def notice(self, text: str) -> None: ...

    def poll_key(self) -> str | None: ...

    def leave_qr(self) -> None: ...


_AUTHORIZED, _PHONE, _PASSWORD, _EXPIRED = "authorized", "phone", "password", "expired"


def status_payload(config: Config, *, authorized: Any, account: dict | None) -> dict:
    """What the login state looks like from outside. Never carries a credential."""
    has_credentials = bool(config.api_id and config.api_hash)
    payload: dict[str, Any] = {
        "credentials": "present" if has_credentials else "missing",
        "credential_source": config.credential_source if has_credentials else "missing",
        "session_file": "present" if config.session_path.exists() else "missing",
        "authorized": authorized,
        "state_dir": str(config.state_dir),
    }
    if account:
        payload["account"] = account
    if not has_credentials:
        payload["hint"] = str(MissingCredentials())
    elif authorized is False:
        payload["hint"] = f"not logged in — run {COMMAND} --browser"
    return payload


def write_status(config: Config, payload: dict) -> dict:
    """Stamped with time and writer: a poller must be able to tell a stale file, and a
    login whose process died, from one still in progress."""
    path = config.auth_status_path
    stamped = redact_value(
        {**payload, "pid": os.getpid(), "written": datetime.now(timezone.utc).isoformat()},
        credentials=(config.api_id, config.api_hash),
    )
    write_private_text(path, private_json(stamped, ensure_ascii=False, indent=2) + "\n")
    return stamped


def _publish(config: Config, step: str) -> None:
    write_status(config, {"state": "waiting", "step": step})


async def interactive_login(
    config: Config, client: Any, ui: Ui, pace: Pace = DEFAULT_PACE,
    *, method: str | None = None, can_prompt: bool = True,
) -> dict:
    try:
        await client.connect()
        # Telethon creates the session file with the plain umask, and the auth key lands
        # in it during the login — tighten before that, not after.
        _tighten(config.session_path)
        if await client.is_user_authorized():
            return _authorized(config, await _describe_me(client), already=True)

        if method is None:
            _publish(config, "method")
            method = _choose_method(ui)
        outcome = _PASSWORD if method == "resume" else method
        if outcome == "qr":
            _publish(config, "qr")
            outcome = await _qr_phase(client, ui, pace)
        if outcome == _EXPIRED:
            minutes = max(round(pace.limit / 60), 1)
            return write_status(config, {
                "state": "expired",
                "hint": f"no one scanned the code in {minutes} min; run {COMMAND} again",
            })
        if outcome == _PHONE:
            _publish(config, "phone")
            outcome = await _phone_phase(config, client, ui, pace.attempts)
        if outcome == _PASSWORD:
            if not can_prompt:
                return write_status(config, {
                    "state": "needs_password",
                    "hint": (
                        "QR approved; this account needs its two-step password. "
                        f"Run {COMMAND} --browser --resume for private input. "
                        "The QR does not need to be scanned again."
                    ),
                })
            _publish(config, "password")
            await _password_phase(client, ui, pace.attempts)
        return _authorized(config, await _describe_me(client))
    finally:
        _tighten(config.session_path)
        await client.disconnect()


def _choose_method(ui: Ui) -> str:
    ui.say("Choose how to log in to Telegram:")
    ui.say("  1. Phone number and login code (password if two-step verification is enabled)")
    ui.say("  2. QR code shown here")
    ui.say("Ctrl-C cancels.")
    while True:
        choice = ui.ask("Choose login method [1/2]: ").strip().casefold()
        if choice in {"1", "p", "phone"}:
            return _PHONE
        if choice in {"2", "q", "qr"}:
            return "qr"
        ui.say("Enter 1 for phone number or 2 for QR code.")


async def interactive_logout(config: Config, client: Any, ui: Ui) -> dict:
    try:
        await client.connect()
        _tighten(config.session_path)
        if not await client.is_user_authorized():
            return write_status(config, {"state": "logged_out", "already": True})
        account = await _describe_me(client)
        _publish(config, "logout_confirmation")
        answer = ui.ask(
            f"Log out {account['name']} (id {account['id']}) from this plugin? [y/N]: "
        ).strip().casefold()
        if answer not in {"y", "yes"}:
            return write_status(config, {"state": "cancelled", "hint": "logout cancelled"})
        _publish(config, "logout")
        if not await client.log_out():
            raise LoginFailed("Telegram did not confirm logout; the session was kept")
        return write_status(config, {"state": "logged_out"})
    finally:
        _tighten(config.session_path)
        await client.disconnect()


async def _qr_phase(client: Any, ui: Ui, pace: Pace) -> str:
    qr = await client.qr_login()
    # One wait spans every rotation. Its update handler stays registered throughout, so a
    # scan that lands while the next token is being fetched is still caught.
    waiter = asyncio.ensure_future(qr.wait(pace.limit + pace.grace + pace.max_show))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + pace.limit
    offset = _server_offset(client)
    rotate_at: float | None = loop.time() + _screen_time(qr, pace, offset)

    def seconds_left() -> int:
        return max(math.ceil(rotate_at - pace.rotate_after - loop.time()), 0)

    shown = seconds_left()
    url: str | None = qr.url
    retry = pace.retry
    ui.enter_qr()
    try:
        ui.show_qr(url, shown)
        while not waiter.done():
            if rotate_at is not None and _wants_phone(ui.poll_key()):
                return _PHONE
            now = loop.time()
            if now >= deadline:
                # A scan may be mid-confirmation (a DC switch takes seconds); let it land.
                await asyncio.wait({waiter}, timeout=pace.grace)
                if not waiter.done():
                    return _EXPIRED
                break
            if rotate_at is not None and now >= rotate_at:
                try:
                    await qr.recreate()
                except SessionPasswordNeededError:
                    return _PASSWORD
                previous, url = url, _live_url(qr)
                if url is None:
                    # Accepted between two tokens; the waiter receives the confirmation.
                    rotate_at = None
                    ui.notice("Confirmed on your phone, finishing…")
                elif url == previous:
                    rotate_at = loop.time() + retry
                    retry = min(retry * 2, pace.max_retry)
                else:
                    retry = pace.retry
                    rotate_at = loop.time() + _screen_time(qr, pace, offset)
                    shown = seconds_left()
                    ui.show_qr(url, shown)
            if rotate_at is not None and seconds_left() != shown:
                shown = seconds_left()
                ui.countdown(shown)
            await asyncio.wait({waiter}, timeout=pace.tick)
        try:
            waiter.result()
        except SessionPasswordNeededError:
            return _PASSWORD
        except asyncio.TimeoutError:
            return _EXPIRED
        return _AUTHORIZED
    finally:
        if not waiter.done():
            waiter.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await waiter
        ui.leave_qr()


def _screen_time(qr: Any, pace: Pace, offset: float = 0.0) -> float:
    server_now = datetime.now(timezone.utc) + timedelta(seconds=offset)
    left = (qr.expires - server_now).total_seconds()
    return min(max(left + pace.rotate_after, pace.min_show), pace.max_show)


def _server_offset(client: Any) -> float:
    """Telethon's measure of how far Telegram's clock is ahead of ours; 0 when unknown."""
    state = getattr(getattr(client, "_sender", None), "_state", None)
    offset = getattr(state, "time_offset", 0)
    return float(offset) if isinstance(offset, (int, float)) else 0.0


def _wants_phone(keys: str | None) -> bool:
    # з shares the key with p on the Russian layout.
    return bool(keys) and any(key in "pPзЗ" for key in keys)


def _live_url(qr: Any) -> str | None:
    """None once the export answers with a success or a DC migration instead of a token."""
    try:
        qr.expires  # noqa: B018 — a consumed token has no expiry
        return qr.url
    except AttributeError:
        return None


async def _phone_phase(config: Config, client: Any, ui: Ui, attempts: int) -> str:
    ui.say("Log in with your phone number. Ctrl-C cancels.")
    while True:
        raw = ui.ask("Phone number, with country code: ").strip()
        phone = utils.parse_phone(raw) if raw else None
        if not phone:
            ui.say("That is not a phone number. Include the country code, e.g. +44 7700 900123.")
            continue
        try:
            sent = await _send_code(client, phone)
        except PhoneNumberInvalidError:
            ui.say("Telegram does not accept that number. Check the country code.")
            continue
        break

    _publish(config, "code")
    ui.say(_where_the_code_went(sent))
    wrong = 0
    while True:
        code = "".join(ui.ask_secret("Login code (hidden as you type): ").split()).replace("-", "")
        if not code:
            continue
        expired = False
        try:
            await client.sign_in(phone, code=code)
            return _AUTHORIZED
        except SessionPasswordNeededError:
            return _PASSWORD
        except (PhoneCodeInvalidError, PhoneCodeEmptyError) as exc:
            wrong += 1
            if wrong >= attempts:
                raise LoginFailed(f"the login code was wrong {attempts} times") from exc
            ui.say(f"That code is wrong. {_tries_left(attempts - wrong)}")
        except PhoneCodeExpiredError:
            ui.say("That code has expired. Sending a new one.")
            expired = True
        except PhoneNumberUnoccupiedError as exc:
            raise LoginFailed("no Telegram account uses this phone number") from exc
        except FloodWaitError as exc:
            raise LoginFailed(_flood(exc)) from exc
        if expired:
            # Outside the handler, so a refusal here is reported like any other.
            ui.say(_where_the_code_went(await _send_code(client, phone)))


async def _send_code(client: Any, phone: str) -> Any:
    try:
        return await client.send_code_request(phone)
    except PhoneNumberBannedError as exc:
        raise LoginFailed("Telegram has banned this phone number") from exc
    except FloodWaitError as exc:
        raise LoginFailed(_flood(exc)) from exc


async def _password_phase(client: Any, ui: Ui, attempts: int) -> None:
    hint = await _password_hint(client)
    ui.say("This account has two-step verification.")
    if hint:
        ui.say(f"Password hint: {hint}")
    wrong = 0
    while True:
        password = ui.ask_secret("Password (hidden as you type): ")
        if not password:
            ui.say("Type the password, or press Ctrl-C to cancel.")
            continue
        try:
            await client.sign_in(password=password)
            return
        except PasswordHashInvalidError as exc:
            wrong += 1
            if wrong >= attempts:
                raise LoginFailed(f"the two-step password was wrong {attempts} times") from exc
            ui.say(f"Wrong password. {_tries_left(attempts - wrong)}")
        except FloodWaitError as exc:
            raise LoginFailed(_flood(exc)) from exc


async def _password_hint(client: Any) -> str | None:
    try:
        return (await client(GetPasswordRequest())).hint or None
    except RPCError:
        return None


def _where_the_code_went(sent: Any) -> str:
    kind = getattr(sent, "type", None)
    if isinstance(kind, SentCodeTypeApp):
        return "Telegram sent the code to your Telegram app, in the chat named Telegram."
    if isinstance(kind, SentCodeTypeSms):
        return "Telegram sent the code by SMS."
    if isinstance(kind, SentCodeTypeCall):
        return "Telegram is calling you with the code."
    return "Telegram sent you a login code."


def _tries_left(count: int) -> str:
    return "1 try left." if count == 1 else f"{count} tries left."


def _flood(exc: FloodWaitError) -> str:
    return f"Telegram asks to wait {exc.seconds} s before the next login attempt"


def _authorized(config: Config, account: dict, *, already: bool = False) -> dict:
    _tighten(config.session_path)
    payload: dict[str, Any] = {"state": "authorized", "account": account}
    if already:
        payload["already"] = True
    return write_status(config, payload)


async def _describe_me(client: Any) -> dict:
    me = await client.get_me()
    return {"id": me.id, "name": utils.get_display_name(me)}


def _tighten(path: Path) -> None:
    tighten_private_file(path)


async def _report_status(config: Config) -> dict:
    # Quick to give up: a status that retries for a minute is a worse answer than "unknown".
    prepare_session_file(config.session_path)
    client = TelegramClient(
        str(config.session_path), config.api_id, config.api_hash,
        connection_retries=1, retry_delay=0, timeout=10,
    )
    try:
        await client.connect()
        authorized = await client.is_user_authorized()
        account = await _describe_me(client) if authorized else None
    finally:
        await client.disconnect()
    return status_payload(config, authorized=authorized, account=account)


def _status_command(config: Config) -> int:
    """Always answers with JSON and exit 0: what cannot be checked is reported, not raised."""
    if not (config.api_id and config.api_hash) or not config.session_path.exists():
        payload = status_payload(config, authorized=False, account=None)
    else:
        try:
            # Not waiting: "someone is using it" is the answer, and a status command that
            # blocks for twenty seconds is a worse one.
            with session_lock(config.session_path):
                payload = asyncio.run(_report_status(config))
        except SessionLocked:
            payload = status_payload(config, authorized="unknown", account=None)
            payload["session_in_use"] = True
            payload["hint"] = (
                "another process holds this session — almost certainly another Telegram "
                "command or agent, which means a session exists. Let it finish to re-check "
                "whether the session is still authorised."
            )
        except (OSError, sqlite3.Error, asyncio.TimeoutError) as exc:
            payload = status_payload(config, authorized="unknown", account=None)
            payload["hint"] = (
                f"could not open the session ({describe(exc)}). Checking it needs write access "
                f"to {config.state_dir} and a connection to Telegram; inside an agent's "
                "sandbox, rerun this command outside it."
            )
    print(private_json(payload, ensure_ascii=False, indent=2))
    return 0


def _read_status(config: Config) -> dict | None:
    try:
        payload = json.loads(read_private_text(config.auth_status_path))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _written(payload: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(payload["written"])
    except (KeyError, TypeError, ValueError):
        return None


def _after(written: datetime | None, started: datetime) -> bool:
    if written is None:
        return False
    if written.microsecond == 0:  # the shell launcher stamps to the second
        started = started.replace(microsecond=0)
    return written >= started


def _alive(pid: Any) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone we may not signal
    return True


def _session_held(config: Config) -> bool | None:
    """Whether some process holds the session lock, as a login does from its first status
    write to its last. Read-only, so it works in a sandbox; None when it cannot tell."""
    try:
        descriptor = open_private_file(Path(f"{config.session_path}.lock"), tighten=False)
    except FileNotFoundError:
        return False
    except OSError:
        return None
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    except OSError:
        return None
    else:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        return False
    finally:
        os.close(descriptor)


def _login_running(config: Config, pid: Any) -> bool:
    """The lock first: a pid is invisible from inside a sandbox with its own namespace."""
    held = _session_held(config)
    return _alive(pid) if held is None else held


def _wait_command(
    config: Config, timeout: float, *, poll: float = 1.0, settle: float = 2.0
) -> int:
    """Only an outcome written after this started, or by a login seen running, counts:
    an `authorized` left by yesterday's run must not read as today's success."""
    started = datetime.now(timezone.utc)
    deadline = time.monotonic() + timeout
    followed: int | None = None
    gone_since: float | None = None
    while True:
        payload = _read_status(config)
        if payload is not None:
            written = _written(payload)
            pid = payload.get("pid")
            relevant = _after(written, started) or (followed is not None and pid == followed)
            if payload.get("state") in FINAL_STATES:
                if relevant:
                    print(private_json(payload, ensure_ascii=False, indent=2))
                    if payload["state"] in {"authorized", "logged_out"}:
                        return 0
                    return 4 if payload["state"] == "needs_password" else 1
            elif _login_running(config, pid):
                followed, gone_since = pid, None
            elif relevant and gone_since is None:
                # A failing login lets go of the lock a moment before it writes why.
                gone_since = time.monotonic()
            elif relevant and time.monotonic() - gone_since >= settle:
                print(private_json({
                    "state": "failed",
                    "abandoned": True,
                    "hint": (
                        "the login ended without reporting an outcome, most likely because "
                        f"its terminal was closed; run {COMMAND} again"
                    ),
                }, indent=2))
                return 1
        if time.monotonic() >= deadline:
            print(private_json({
                "state": "timeout",
                "last": payload,
                "hint": (
                    f"no login finished within {timeout:.0f} s. Ask the operator whether they "
                    f"ran {COMMAND}, then check --status."
                ),
            }, ensure_ascii=False, indent=2))
            return 3
        time.sleep(poll)


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="telegram-login",
        description=(
            "Manage this plugin's own Telegram session. Without flags: choose phone number "
            "and login code or a QR code shown in this terminal; a two-step password is "
            "asked with hidden input. Nothing secret is ever taken as an argument."
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--status", action="store_true", help="report the login state and exit")
    mode.add_argument("--logout", action="store_true", help="confirm and log out this plugin")
    mode.add_argument("--method", choices=("phone", "qr"), help="start the chosen login method")
    mode.add_argument("--resume", action="store_true", help="finish an approved QR with hidden input")
    mode.add_argument(
        "--wait",
        action="store_true",
        help="wait for a login started elsewhere to finish, then print its outcome",
    )
    mode.add_argument("--qr", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--browser", action="store_true",
                        help="automatically open a private local sign-in or logout form")
    parser.add_argument("--theme", choices=("dark", "light"), default="dark",
                        help="theme for QR frames in command output (default: dark)")
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_WAIT,
        help=f"seconds --wait waits (default {DEFAULT_WAIT:.0f})",
    )
    parser.add_argument("--state-dir", help="override the state directory")
    arguments = parser.parse_args(argv)
    if arguments.browser and (arguments.status or arguments.wait or arguments.qr):
        parser.error("--browser is for login or logout, not status or wait")
    return arguments


def _failed(config: Config, hint: str, *, code: int, state: str = "failed") -> int:
    """An agent waiting on this login reads only the status file, so every way out has to
    leave its outcome there — stderr alone reaches nobody."""
    try:
        write_status(config, {"state": state, "hint": hint})
    except OSError:
        pass  # the state directory is what failed; stderr is still a channel
    _complain(hint)
    return code


def _complain(hint: str) -> None:
    sentence = hint[:1].upper() + hint[1:]
    _say(f"✗ {sentence if sentence.endswith('.') else sentence + '.'}", sys.stderr)


def _say(text: str, stream: Any) -> None:
    try:
        print(redact_text(text), file=stream, flush=True)
    except OSError:
        pass  # the terminal is gone; the outcome is already in the status file


def _finished(result: dict) -> int:
    if result["state"] == "needs_password":
        _say(result["hint"], sys.stdout)
        return 4
    if result["state"] == "logged_out":
        message = "Already logged out." if result.get("already") else "✓ Logged out of Telegram."
        _say(message, sys.stdout)
        return 0
    if result["state"] == "authorized":
        account = result["account"]
        lead = "Already logged in" if result.get("already") else "✓ Logged in"
        _say(f"{lead} as {account['name']} (id {account['id']}). Telegram is ready to use.",
             sys.stdout)
        _say(f"To log out, use /telegram:logout or run {COMMAND} --browser --logout.", sys.stdout)
        return 0
    _complain(result["hint"])
    return 1


@contextlib.contextmanager
def _interrupts() -> Iterator[None]:
    """Ctrl-C, a closed terminal and a kill all raise here, even inside a blocking prompt:
    asyncio.run's own Ctrl-C handler only schedules a cancel that `input()` never sees."""

    def interrupt(reason: str) -> Callable[[int, Any], None]:
        def handler(signum: int, frame: Any) -> None:
            raise Interrupted(reason)

        return handler

    wanted = {
        signal.SIGINT: interrupt("login cancelled"),
        signal.SIGHUP: interrupt("the terminal was closed"),
        signal.SIGTERM: interrupt("the login was stopped"),
    }
    previous = {number: signal.signal(number, handler) for number, handler in wanted.items()}
    try:
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def _telethon_client(config: Config) -> Any:
    prepare_session_file(config.session_path)
    return TelegramClient(str(config.session_path), config.api_id, config.api_hash)


@private_output
def main(
    argv: list[str] | None = None,
    *,
    client_factory: Callable[[Config], Any] | None = None,
    ui: Ui | None = None,
    pace: Pace = DEFAULT_PACE,
) -> int:
    arguments = _parse(sys.argv[1:] if argv is None else argv)
    environment = dict(os.environ)
    if arguments.state_dir:
        environment["TELEGRAM_STATE_DIR"] = arguments.state_dir
    try:
        config = load_config_from_environment(environment)
    except (OSError, ValueError) as exc:
        if arguments.status:
            print(private_json({"authorized": "unknown", "credentials": "unknown",
                              "hint": f"Could not safely read configuration: {describe(exc)}. "
                              "Check state permissions and sandbox access."}))
            return 0
        _complain(describe(exc))
        return 1

    if arguments.status:
        return _status_command(config)
    if arguments.wait:
        return _wait_command(config, arguments.timeout)
    if arguments.qr:
        return _failed(config, QR_REMOVED, code=2)

    browser_ui = None
    outcome = None

    def fail(hint: str, *, code: int, state: str = "failed") -> int:
        nonlocal outcome
        outcome = {"state": state, "hint": hint}
        return _failed(config, hint, code=code, state=state)

    try:
        ensure_state_dir(config)
        # Taken before anything else, including the credentials check: if a command holds
        # this session, logging in here would put two clients on one auth key and Telegram
        # would revoke it. Waiting, because the holder lets go once it goes idle.
        with session_lock(config.session_path, config.lock_wait):
            if arguments.logout and not config.session_path.exists():
                outcome = write_status(config, {"state": "logged_out", "already": True})
                return _finished(outcome)
            if not (config.api_id and config.api_hash):
                raise MissingCredentials()
            if ui is None:
                from telegram_plugin.terminal import QrOutput, Terminal, is_interactive

                if arguments.browser:
                    from telegram_plugin.browser_login import BrowserUi

                    browser_ui = BrowserUi()
                    ui, can_prompt = browser_ui, True
                else:
                    can_prompt = is_interactive(sys.stdin, sys.stdout)
                    if not can_prompt and arguments.method != "qr":
                        return fail(NEEDS_TERMINAL, code=1)
                    ui = Terminal() if can_prompt else QrOutput(theme=arguments.theme)
            else:
                can_prompt = True
            # Overwrite any outcome an earlier run left, before anyone starts polling.
            write_status(config, {"state": "starting"})
            client = (client_factory or _telethon_client)(config)
            with _interrupts():
                if arguments.logout:
                    operation = interactive_logout(config, client, ui)
                else:
                    method = "resume" if arguments.resume else arguments.method
                    operation = interactive_login(
                        config, client, ui, pace, method=method, can_prompt=can_prompt,
                    )
                outcome = asyncio.run(browser_ui.run(operation) if browser_ui else operation)
            return _finished(outcome)
    except SessionLocked as exc:
        return fail(describe(exc), code=1)
    except MissingCredentials as exc:
        return fail(describe(exc), code=2)
    except LoginFailed as exc:
        command = f"{COMMAND} --logout" if arguments.logout else COMMAND
        return fail(f"{exc.hint}; run {command} again to retry", code=1)
    except Interrupted as exc:
        hint = "logout interrupted; check --status" if arguments.logout else (
            f"{exc.reason}; check --status before retrying"
        )
        return fail(hint, code=130, state="cancelled")
    except (EOFError, KeyboardInterrupt):
        hint = "logout interrupted; check --status" if arguments.logout else (
            "login cancelled; check --status before retrying"
        )
        return fail(hint, code=130, state="cancelled")
    except Exception as exc:  # noqa: BLE001
        return fail(describe(exc), code=1)
    finally:
        if browser_ui is not None:
            try:
                browser_ui.finish(outcome or {"state": "failed"})
            finally:
                browser_ui.close()


if __name__ == "__main__":
    raise SystemExit(main())
