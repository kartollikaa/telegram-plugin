"""The login flow's own state machine, against a stub of the four Telethon calls
it makes. No network, and no interactive input anywhere in these paths."""

import asyncio
import json
import stat
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from telethon.errors import SessionPasswordNeededError

from telegram_plugin.config import load_config
from telegram_plugin.login import qr_login, status_payload, write_status


class StubQr:
    def __init__(self, url="tg://login?token=AAAA", outcome="ok", expires_in=30):
        self.url = url
        self._outcome = outcome
        self.recreated = 0
        self.expires = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    async def wait(self, timeout=None):
        if self._outcome == "timeout":
            raise asyncio.TimeoutError
        if self._outcome == "password":
            raise SessionPasswordNeededError(request=None)
        return SimpleNamespace(id=42)

    async def recreate(self):
        # Async because Telethon's QRLogin.recreate is a coroutine — a sync double
        # here let a missing `await` ship, and the live run caught what the test
        # could not. A recreated link is a fresh unscanned link, so the outcome is
        # unchanged: otherwise a test for "never confirmed" confirms itself.
        self.recreated += 1


class StubClient:
    def __init__(self, outcome="ok", authorized=False, expires_in=30):
        self.qr = StubQr(outcome=outcome, expires_in=expires_in)
        self._authorized = authorized
        self.disconnected = False

    async def connect(self):
        return True

    async def is_user_authorized(self):
        return self._authorized

    async def qr_login(self):
        return self.qr

    async def get_me(self):
        return SimpleNamespace(id=42, first_name="Test", last_name=None, username="tester")

    async def disconnect(self):
        self.disconnected = True


def _config(tmp_path, **extra):
    return load_config({"TELEGRAM_STATE_DIR": str(tmp_path), "TELEGRAM_API_ID": "1",
                        "TELEGRAM_API_HASH": "h", **extra})


async def test_a_scanned_link_authorises_and_reports_the_account(tmp_path):
    config = _config(tmp_path)
    client = StubClient()
    result = await qr_login(config, client, timeout=1)
    assert result["state"] == "authorized"
    assert result["account"]["id"] == 42
    assert client.disconnected is True


async def test_the_link_is_published_before_the_wait_begins(tmp_path):
    config = _config(tmp_path)
    seen = []

    class Watching(StubClient):
        async def qr_login(self):
            qr = await super().qr_login()

            async def wait(timeout=None):
                seen.append(json.loads(config.auth_status_path.read_text()))
                return SimpleNamespace(id=42)

            qr.wait = wait
            return qr

    await qr_login(config, Watching(), timeout=1)
    assert seen and seen[0]["state"] == "waiting"
    assert seen[0]["url"].startswith("tg://login?token=")


async def test_two_factor_accounts_are_handed_to_the_terminal(tmp_path):
    config = _config(tmp_path)
    result = await qr_login(config, StubClient(outcome="password"), timeout=1)
    assert result["state"] == "needs_password"
    assert "telegram-login" in result["hint"]
    assert "Traceback" not in json.dumps(result)


async def test_a_spent_token_is_reissued_before_the_next_wait(tmp_path):
    config = _config(tmp_path)
    client = StubClient(outcome="timeout", expires_in=-1)
    result = await qr_login(config, client, timeout=0.01, attempts=3)
    assert result["state"] == "expired"
    assert client.qr.recreated == 2, "one reissue per retry once the token is spent"


async def test_a_live_token_is_not_reissued_needlessly(tmp_path):
    config = _config(tmp_path)
    client = StubClient(outcome="timeout", expires_in=300)
    result = await qr_login(config, client, timeout=0.01, attempts=3)
    assert result["state"] == "expired"
    assert client.qr.recreated == 0, "re-requesting a live login token costs an API call"


async def test_the_status_file_is_private(tmp_path):
    config = _config(tmp_path)
    config.state_dir.mkdir(parents=True, exist_ok=True)
    write_status(config, {"state": "waiting"})
    assert stat.S_IMODE(config.auth_status_path.stat().st_mode) == 0o600


def test_status_payload_never_carries_the_credentials(tmp_path):
    config = _config(tmp_path, TELEGRAM_API_HASH="planted-api-hash-value")
    payload = status_payload(config, authorized=False, account=None)
    assert "planted-api-hash-value" not in json.dumps(payload)
    assert payload["credentials"] == "present"
    assert payload["authorized"] is False


def test_status_payload_says_when_credentials_are_missing(tmp_path):
    config = load_config({"TELEGRAM_STATE_DIR": str(tmp_path)})
    payload = status_payload(config, authorized=False, account=None)
    assert payload["credentials"] == "missing"
    assert "my.telegram.org" in payload["hint"]


def test_status_payload_reports_the_account_when_authorised(tmp_path):
    payload = status_payload(_config(tmp_path), authorized=True,
                             account={"id": 42, "name": "Test"})
    assert payload["authorized"] is True
    assert payload["account"]["id"] == 42
    assert "hint" not in payload


@pytest.mark.parametrize("forbidden", ["--phone", "--code", "--password", "--2fa", "--token"])
def test_no_secret_bearing_options_were_added(forbidden):
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "src/telegram_plugin/login.py").read_text()
    assert forbidden not in source


def _hold_the_session(path, ready, release):
    """Module level so multiprocessing can pickle it under spawn."""
    from telegram_plugin.client import session_lock

    with session_lock(path):
        ready.set()
        release.wait(30)


def test_status_answers_instead_of_failing_when_the_session_is_in_use(tmp_path):
    """A running MCP server holds the lock; asking for status must still answer."""
    import multiprocessing as mp
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    session = tmp_path / "telegram.session"
    ready, release = mp.Event(), mp.Event()
    holder = mp.Process(target=_hold_the_session, args=(session, ready, release))
    holder.start()
    try:
        assert ready.wait(10)
        finished = subprocess.run(
            [sys.executable, "-m", "telegram_plugin.login", "--status"],
            capture_output=True,
            text=True,
            env={
                "PYTHONPATH": str(repo / "src"),
                "TELEGRAM_STATE_DIR": str(tmp_path),
                "HOME": str(tmp_path),
            },
            timeout=120,
            check=False,
        )
        assert finished.returncode == 0
        payload = json.loads(finished.stdout)
        assert payload["session_in_use"] is True
        assert payload["authorized"] == "unknown"
        assert "Traceback" not in finished.stderr
    finally:
        release.set()
        holder.join(10)


def test_the_double_matches_telethon_on_which_calls_are_coroutines():
    """This is the guard that was missing: a sync double for an async API let a
    missing `await` through, and only a live run found it."""
    import inspect

    from telethon.tl.custom.qrlogin import QRLogin

    for name in ("wait", "recreate"):
        real = getattr(QRLogin, name)
        stub = getattr(StubQr, name)
        assert inspect.iscoroutinefunction(real), f"telethon changed: {name} is no longer async"
        assert inspect.iscoroutinefunction(stub), f"the double diverges from telethon on {name}"
