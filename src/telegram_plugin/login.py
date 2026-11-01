"""Authorisation, deliberately out of process and in front of a human.

* no flags — the login itself, in a terminal: a QR code redrawn in place as its token
  rotates, one key to switch to phone and code, a hidden prompt for a two-step password.
* `--status` — whether the session is usable, and under whom. Changes nothing.
* `--wait` — blocks until a login running elsewhere reports its outcome, so an agent
  can hand the operator the command and learn when they are done.

Every way out of the login leaves its outcome in `auth-status.json`.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import math
import os
import signal
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
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

FINAL_STATES = frozenset({"authorized", "cancelled", "failed", "expired"})
DEFAULT_WAIT = 480.0
COMMAND = "bin/telegram-login"

NEEDS_TERMINAL = (
    f"{COMMAND} is interactive: it shows a QR code and asks for a password if the account "
    "has one, so it needs a terminal. Run it in a terminal window, or press Run under the "
    "command block in bb chat."
)
QR_REMOVED = (
    f"--qr was removed: a link cannot be opened on the device that shows it, and it cannot "
    f"finish a two-step login. Run {COMMAND} without flags in a terminal."
)


@dataclass(frozen=True)
class Pace:
    """How the QR screen spends time. Telegram issues login tokens for about 30 seconds."""

    rotate_before: float = 5.0
    """Replace the token this long before it expires, so the code on screen is never dead."""
    min_show: float = 10.0
    """Floor on a token's screen time: a clock running ahead of Telegram's would otherwise
    make every fresh token look expired and turn the loop into a request storm."""
    max_show: float = 60.0
    limit: float = 300.0
    """Give up on the QR after this long without a scan; the operator reruns the command."""
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
    elif authorized is False:
        payload["hint"] = f"not logged in — run {COMMAND} in a terminal"
    return payload


def write_status(config: Config, payload: dict) -> dict:
    """Stamped with time and writer: a poller must be able to tell a stale file, and a
    login whose process died, from one still in progress."""
    path = config.auth_status_path
    path.parent.mkdir(parents=True, exist_ok=True)
    stamped = {**payload, "pid": os.getpid(), "written": datetime.now(timezone.utc).isoformat()}
    path.write_text(json.dumps(stamped, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return stamped


def _publish(config: Config, step: str) -> None:
    write_status(config, {"state": "waiting", "step": step})


async def interactive_login(config: Config, client: Any, ui: Ui, pace: Pace = DEFAULT_PACE) -> dict:
    try:
        await client.connect()
        # Telethon creates the session file with the plain umask, and the auth key lands
        # in it during the login — tighten before that, not after.
        _tighten(config.session_path)
        if await client.is_user_authorized():
            return _authorized(config, await _describe_me(client), already=True)

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
            _publish(config, "password")
            await _password_phase(client, ui, pace.attempts)
        return _authorized(config, await _describe_me(client))
    finally:
        _tighten(config.session_path)
        await client.disconnect()


async def _qr_phase(client: Any, ui: Ui, pace: Pace) -> str:
    qr = await client.qr_login()
    # One wait spans every rotation. Its update handler stays registered throughout, so a
    # scan that lands while the next token is being fetched is still caught.
    waiter = asyncio.ensure_future(qr.wait(pace.limit + pace.max_show))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + pace.limit
    rotate_at: float | None = loop.time() + _screen_time(qr, pace)
    shown = math.ceil(rotate_at - loop.time())
    ui.enter_qr()
    try:
        ui.show_qr(qr.url, shown)
        while not waiter.done():
            if (ui.poll_key() or "").lower() == "p":
                return _PHONE
            now = loop.time()
            if now >= deadline:
                return _EXPIRED
            if rotate_at is not None and now >= rotate_at:
                try:
                    await qr.recreate()
                except SessionPasswordNeededError:
                    return _PASSWORD
                url = _live_url(qr)
                if url is None:
                    # Accepted between two tokens; the waiter receives the confirmation.
                    rotate_at = None
                    ui.notice("Confirmed on your phone, finishing…")
                else:
                    rotate_at = loop.time() + _screen_time(qr, pace)
                    shown = math.ceil(rotate_at - loop.time())
                    ui.show_qr(url, shown)
            if rotate_at is not None and math.ceil(rotate_at - loop.time()) != shown:
                shown = max(math.ceil(rotate_at - loop.time()), 0)
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


def _screen_time(qr: Any, pace: Pace) -> float:
    left = (qr.expires - datetime.now(timezone.utc)).total_seconds()
    return min(max(left - pace.rotate_before, pace.min_show), pace.max_show)


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
            sent = await client.send_code_request(phone)
        except PhoneNumberInvalidError:
            ui.say("Telegram does not accept that number. Check the country code.")
            continue
        except PhoneNumberBannedError as exc:
            raise LoginFailed("Telegram has banned this phone number") from exc
        except FloodWaitError as exc:
            raise LoginFailed(_flood(exc)) from exc
        break

    _publish(config, "code")
    ui.say(_where_the_code_went(sent))
    wrong = 0
    while True:
        code = "".join(ui.ask_secret("Login code (hidden as you type): ").split()).replace("-", "")
        if not code:
            continue
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
            ui.say(_where_the_code_went(await client.send_code_request(phone)))
        except PhoneNumberUnoccupiedError as exc:
            raise LoginFailed("no Telegram account uses this phone number") from exc
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
    if path.exists():
        path.chmod(0o600)


async def _report_status(config: Config) -> dict:
    # Quick to give up: a status that retries for a minute is a worse answer than "unknown".
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
        except (OSError, asyncio.TimeoutError) as exc:
            payload = status_payload(config, authorized="unknown", account=None)
            payload["hint"] = (
                f"could not open the session ({describe(exc)}). Checking it needs write access "
                f"to {config.state_dir} and a connection to Telegram; inside an agent's "
                "sandbox, rerun this command outside it."
            )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _read_status(config: Config) -> dict | None:
    try:
        payload = json.loads(config.auth_status_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _written(payload: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(payload["written"])
    except (KeyError, TypeError, ValueError):
        return None


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


def _wait_command(config: Config, timeout: float, *, poll: float = 1.0) -> int:
    """Only an outcome written after this started, or by a login seen running, counts:
    an `authorized` left by yesterday's run must not read as today's success."""
    started = datetime.now(timezone.utc)
    deadline = time.monotonic() + timeout
    followed: int | None = None
    while True:
        payload = _read_status(config)
        if payload is not None:
            written = _written(payload)
            pid = payload.get("pid")
            relevant = (written is not None and written >= started) or (
                followed is not None and pid == followed
            )
            if payload.get("state") in FINAL_STATES:
                if relevant:
                    print(json.dumps(payload, ensure_ascii=False, indent=2))
                    return 0 if payload["state"] == "authorized" else 1
            elif _alive(pid):
                followed = pid
            elif relevant:
                print(json.dumps({
                    "state": "failed",
                    "abandoned": True,
                    "hint": (
                        "the login ended without reporting an outcome, most likely because "
                        f"its terminal was closed; run {COMMAND} again"
                    ),
                }, indent=2))
                return 1
        if time.monotonic() >= deadline:
            print(json.dumps({
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
            "Log this plugin's own Telegram session in. Without flags: scan a QR code shown "
            "in the terminal, or press p for phone number and code; a two-step password is "
            "asked with hidden input. Nothing secret is ever taken as an argument."
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--status", action="store_true", help="report the login state and exit")
    mode.add_argument(
        "--wait",
        action="store_true",
        help="wait for a login started elsewhere to finish, then print its outcome",
    )
    mode.add_argument("--qr", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_WAIT,
        help=f"seconds --wait waits (default {DEFAULT_WAIT:.0f})",
    )
    parser.add_argument("--state-dir", help="override the state directory")
    return parser.parse_args(argv)


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
    print(f"✗ {sentence if sentence.endswith('.') else sentence + '.'}", file=sys.stderr)


def _finished(result: dict) -> int:
    if result["state"] == "authorized":
        account = result["account"]
        lead = "Already logged in" if result.get("already") else "✓ Logged in"
        print(f"{lead} as {account['name']} (id {account['id']}). Telegram is ready to use.")
        return 0
    _complain(result["hint"])
    return 1


@contextlib.contextmanager
def _interrupts() -> Iterator[None]:
    """Ctrl-C has to work at a blocking prompt too. asyncio.run's own handler only
    schedules a cancel, which an `input()` never sees, so it is replaced here; a closed
    terminal or a kill gets the same treatment, so neither leaves a silent outcome."""

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
    return TelegramClient(str(config.session_path), config.api_id, config.api_hash)


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
    config = load_config_from_environment(environment)

    if arguments.status:
        return _status_command(config)
    if arguments.wait:
        return _wait_command(config, arguments.timeout)
    if arguments.qr:
        return _failed(config, QR_REMOVED, code=2)

    try:
        ensure_state_dir(config)
        # Taken before anything else, including the credentials check: if a command holds
        # this session, logging in here would put two clients on one auth key and Telegram
        # would revoke it. Waiting, because the holder lets go once it goes idle.
        with session_lock(config.session_path, config.lock_wait):
            if not (config.api_id and config.api_hash):
                raise MissingCredentials()
            if ui is None:
                if not (sys.stdin.isatty() and sys.stdout.isatty()):
                    return _failed(config, NEEDS_TERMINAL, code=1)
                from telegram_plugin.terminal import Terminal

                ui = Terminal()
            # Overwrite any outcome an earlier run left, before anyone starts polling.
            write_status(config, {"state": "starting"})
            client = (client_factory or _telethon_client)(config)
            with _interrupts():
                result = asyncio.run(interactive_login(config, client, ui, pace))
            return _finished(result)
    except SessionLocked as exc:
        return _failed(config, describe(exc), code=1)
    except MissingCredentials as exc:
        return _failed(config, describe(exc), code=2)
    except LoginFailed as exc:
        return _failed(config, f"{exc.hint}; run {COMMAND} again to retry", code=1)
    except Interrupted as exc:
        return _failed(config, f"{exc.reason}, no account was connected",
                       code=130, state="cancelled")
    except (EOFError, KeyboardInterrupt):
        return _failed(config, "login cancelled, no account was connected",
                       code=130, state="cancelled")
    except Exception as exc:  # noqa: BLE001
        return _failed(config, describe(exc), code=1)


if __name__ == "__main__":
    raise SystemExit(main())
