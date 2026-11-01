"""The login's state machine against stand-ins for Telethon and the operator.
No network and no real terminal; the pty tests cover the terminal itself."""

import json
import os
import stat
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from telegram_plugin import login
from telegram_plugin.config import load_config
from telegram_plugin.login import (
    Interrupted,
    LoginFailed,
    Pace,
    interactive_login,
    main,
    status_payload,
    write_status,
)
from tests.login_doubles import ScriptedUi, StubClient, StubQr

# Seconds shrunk so a rotation takes a fraction of a second; the ratios stay realistic.
FAST = Pace(rotate_after=0.05, retry=0.05, max_retry=0.2, min_show=0.05, max_show=5.0,
            limit=5.0, grace=0.05, tick=0.01)


def _config(tmp_path, **extra):
    return load_config({"TELEGRAM_STATE_DIR": str(tmp_path), "TELEGRAM_API_ID": "1",
                        "TELEGRAM_API_HASH": "h", **extra})


def _status(config):
    return json.loads(config.auth_status_path.read_text())


@pytest.fixture
def steps(monkeypatch):
    """Every state the login publishes, in order."""
    seen = []
    real = login.write_status

    def recording(config, payload):
        seen.append(payload.get("step") or payload["state"])
        return real(config, payload)

    monkeypatch.setattr(login, "write_status", recording)
    return seen


# --- the QR code and its token -------------------------------------------------------


async def test_a_scanned_code_logs_in_and_reports_the_account(tmp_path):
    config = _config(tmp_path)
    client = StubClient(StubQr(scan_after=0.05))
    result = await interactive_login(config, client, ScriptedUi(), FAST)
    assert result["state"] == "authorized"
    assert result["account"] == {"id": 42, "name": "Test"}
    assert _status(config)["state"] == "authorized"
    assert client.disconnected


async def test_the_token_is_replaced_as_it_expires_and_the_code_redrawn(tmp_path):
    """The defect this replaces: the old loop waited 60 s on a 30 s token, so the code
    on screen was dead for most of every attempt. Telegram returns the same token until
    it expires, so the next one is asked for right after, never long after."""
    qr = StubQr(lifetime=0.4, scan_after=1.5)
    ui = ScriptedUi()
    await interactive_login(_config(tmp_path), StubClient(qr), ui, FAST)

    assert len(qr.recreations) >= 3, qr.recreations
    assert len(qr.recreations) == qr.tokens - 1, "asked while the token was still alive"
    for left in qr.recreations:
        assert left <= 0, "asked before expiry, which only gets the same token back"
        assert left >= -(FAST.rotate_after + 0.1), "a dead code stayed on screen"
    assert ui.shown() == [f"tg://login?token=TOKEN{n}" for n in range(1, qr.tokens + 1)]


async def test_one_wait_spans_every_rotation(tmp_path):
    """A wait per token left a gap between waits in which a scan went unheard."""
    qr = StubQr(lifetime=0.3, scan_after=0.9)
    await interactive_login(_config(tmp_path), StubClient(qr), ScriptedUi(), FAST)
    assert len(qr.recreations) >= 2
    assert qr.wait_calls == 1


async def test_the_countdown_runs_down_to_the_next_code(tmp_path):
    qr = StubQr(lifetime=2.0, scan_after=1.2)
    ui = ScriptedUi()
    await interactive_login(_config(tmp_path), StubClient(qr), ui, FAST)
    shown = [event[2] for event in ui.events if event[0] == "qr"]
    counted = [event[1] for event in ui.events if event[0] == "countdown"]
    assert shown[0] == 2
    assert counted and counted == sorted(counted, reverse=True)


async def test_a_clock_ahead_of_telegram_backs_off_and_redraws_only_new_codes(tmp_path):
    """Here every token looks expired on arrival, and the server keeps handing it back."""
    qr = StubQr(lifetime=0.6, scan_after=1.5, server_offset=-0.5)
    ui = ScriptedUi()
    pace = Pace(rotate_after=0.05, retry=0.05, max_retry=0.2, min_show=0.1, max_show=5.0,
                limit=5.0, tick=0.01)
    await interactive_login(_config(tmp_path), StubClient(qr), ui, pace)
    assert qr.tokens >= 2
    assert len(qr.recreations) <= 5 * qr.tokens, qr.recreations
    assert ui.shown() == [f"tg://login?token=TOKEN{n}" for n in range(1, qr.tokens + 1)]


async def test_a_code_accepted_between_tokens_still_finishes(tmp_path):
    qr = StubQr(lifetime=0.3, scan_after=0.6, consumed_on_recreate=1)
    ui = ScriptedUi()
    result = await interactive_login(_config(tmp_path), StubClient(qr), ui, FAST)
    assert result["state"] == "authorized"
    assert any(event[0] == "notice" for event in ui.events)
    assert len(qr.recreations) == 1, "no rotation after the token was consumed"


async def test_an_unscanned_code_gives_up_at_the_limit(tmp_path):
    config = _config(tmp_path)
    qr = StubQr(lifetime=0.3)
    pace = Pace(rotate_after=0.05, min_show=0.05, max_show=5.0, limit=0.5, grace=0.05,
                tick=0.01)
    result = await interactive_login(config, StubClient(qr), ScriptedUi(), pace)
    assert result["state"] == "expired"
    assert "telegram-login" in result["hint"]
    assert qr.wait_cancelled, "the wait outlived the screen it belonged to"


async def test_the_screen_is_left_on_every_way_out_of_the_qr(tmp_path):
    ui = ScriptedUi(answers=[KeyboardInterrupt()], keys=[(0.05, "p")])
    with pytest.raises(KeyboardInterrupt):
        await interactive_login(_config(tmp_path), StubClient(StubQr()), ui, FAST)
    names = [event[0] for event in ui.events]
    assert names.index("leave_qr") < names.index("ask")


async def test_the_session_file_is_tightened_before_the_wait_not_after(tmp_path):
    """Telethon creates it with the plain umask and the auth key lands in it while the
    code is on screen, so chmodding only on success left it readable all that time."""
    config = _config(tmp_path)
    config.state_dir.mkdir(parents=True, exist_ok=True)
    config.session_path.write_bytes(b"")
    config.session_path.chmod(0o644)
    seen = []

    class Watching(ScriptedUi):
        def show_qr(self, url, seconds):
            seen.append(stat.S_IMODE(config.session_path.stat().st_mode))

    await interactive_login(config, StubClient(StubQr(scan_after=0.02)), Watching(), FAST)
    assert seen == [0o600]


# --- phone and code ------------------------------------------------------------------


async def test_p_switches_to_phone_and_code(tmp_path, steps):
    config = _config(tmp_path)
    client = StubClient(StubQr())
    ui = ScriptedUi(answers=["+44 7700 900123"], secrets=["12 345"], keys=[(0.05, "p")])
    result = await interactive_login(config, client, ui, FAST)
    assert result["state"] == "authorized"
    assert client.sent == ["447700900123"]
    assert steps == ["qr", "phone", "code", "authorized"]
    assert client.qr.wait_cancelled


async def test_the_login_code_is_asked_with_hidden_input(tmp_path):
    ui = ScriptedUi(answers=["+447700900123"], secrets=["12345"], keys=[(0.0, "p")])
    await interactive_login(_config(tmp_path), StubClient(StubQr()), ui, FAST)
    assert any(event[0] == "secret" and "code" in event[1].lower() for event in ui.events)


async def test_a_number_telegram_rejects_is_asked_again(tmp_path):
    client = StubClient(StubQr(), invalid_phones={"15550000000"})
    ui = ScriptedUi(answers=["not a phone", "+1 555 000 0000", "+447700900123"],
                    secrets=["12345"], keys=[(0.0, "p")])
    result = await interactive_login(_config(tmp_path), client, ui, FAST)
    assert result["state"] == "authorized"
    assert client.sent == ["15550000000", "447700900123"]


async def test_a_wrong_code_is_asked_again_and_an_expired_one_resent(tmp_path):
    client = StubClient(StubQr(), codes=["wrong", "expired", "ok"])
    ui = ScriptedUi(answers=["+447700900123"], secrets=["11111", "22222", "33333"],
                    keys=[(0.0, "p")])
    result = await interactive_login(_config(tmp_path), client, ui, FAST)
    assert result["state"] == "authorized"
    assert len(client.sent) == 2, "an expired code must be replaced"
    assert any("2 tries left" in line for line in ui.said())


async def test_too_many_wrong_codes_fail_with_a_reason(tmp_path):
    client = StubClient(StubQr(), codes=["wrong"] * 3)
    ui = ScriptedUi(answers=["+447700900123"], secrets=["1", "2", "3"], keys=[(0.0, "p")])
    with pytest.raises(LoginFailed, match="wrong 3 times"):
        await interactive_login(_config(tmp_path), client, ui, FAST)
    assert client.disconnected


# --- two-step password ---------------------------------------------------------------


async def test_a_two_step_account_is_finished_in_the_same_session(tmp_path, steps):
    """Before, the QR flow stopped at needs_password and sent the operator to start over
    in a terminal; now the password is asked right here."""
    client = StubClient(StubQr(scan_after=0.05, outcome="password"), hint="usual one")
    ui = ScriptedUi(secrets=["right"])
    result = await interactive_login(_config(tmp_path), client, ui, FAST)
    assert result["state"] == "authorized"
    assert client.signed_in_with == ["password"]
    assert "Password hint: usual one" in ui.said()
    assert steps == ["qr", "password", "authorized"]


async def test_a_wrong_password_is_asked_again(tmp_path):
    client = StubClient(StubQr(scan_after=0.02, outcome="password"),
                        passwords=["wrong", "wrong", "ok"])
    ui = ScriptedUi(secrets=["nope", "", "still nope", "right"])
    result = await interactive_login(_config(tmp_path), client, ui, FAST)
    assert result["state"] == "authorized"
    assert client.signed_in_with == ["password"] * 3, "an empty answer is not an attempt"
    assert "Wrong password. 2 tries left." in ui.said()
    assert "Wrong password. 1 try left." in ui.said()


async def test_three_wrong_passwords_fail(tmp_path):
    client = StubClient(StubQr(scan_after=0.02, outcome="password"), passwords=["wrong"] * 3)
    ui = ScriptedUi(secrets=["a", "b", "c"])
    with pytest.raises(LoginFailed, match="password was wrong 3 times"):
        await interactive_login(_config(tmp_path), client, ui, FAST)


async def test_the_phone_route_reaches_the_password_too(tmp_path):
    client = StubClient(StubQr(), codes=["password"])
    ui = ScriptedUi(answers=["+447700900123"], secrets=["12345", "right"], keys=[(0.0, "p")])
    result = await interactive_login(_config(tmp_path), client, ui, FAST)
    assert result["state"] == "authorized"
    assert client.signed_in_with == ["code", "password"]


# --- every way out leaves an outcome -------------------------------------------------


def _run_main(tmp_path, client, ui, capsys):
    code = main(["--state-dir", str(tmp_path)], client_factory=lambda _: client, ui=ui,
                pace=FAST)
    out, err = capsys.readouterr()
    return code, json.loads((tmp_path / "auth-status.json").read_text()), out + err


@pytest.fixture
def credentials(monkeypatch):
    monkeypatch.setenv("TELEGRAM_API_ID", "1")
    monkeypatch.setenv("TELEGRAM_API_HASH", "h")


@pytest.mark.parametrize(
    ("client", "ui", "exit_code", "state", "message"),
    [
        (lambda: StubClient(StubQr(scan_after=0.02)), lambda: ScriptedUi(),
         0, "authorized", "Logged in as Test (id 42)"),
        (lambda: StubClient(authorized=True), lambda: ScriptedUi(),
         0, "authorized", "Already logged in as Test (id 42)"),
        (lambda: StubClient(StubQr()), lambda: ScriptedUi(answers=[KeyboardInterrupt()],
                                                          keys=[(0.0, "p")]),
         130, "cancelled", "Login cancelled"),
        (lambda: StubClient(StubQr()), lambda: ScriptedUi(answers=[EOFError()],
                                                          keys=[(0.0, "p")]),
         130, "cancelled", "Login cancelled"),
        (lambda: StubClient(StubQr()),
         lambda: ScriptedUi(answers=[Interrupted("the terminal was closed")], keys=[(0.0, "p")]),
         130, "cancelled", "The terminal was closed"),
        (lambda: StubClient(StubQr(scan_after=0.02, outcome="password"),
                            passwords=["wrong"] * 3),
         lambda: ScriptedUi(secrets=["a", "b", "c"]),
         1, "failed", "password was wrong 3 times"),
        (lambda: StubClient(connect_error=ConnectionError("network is unreachable")),
         lambda: ScriptedUi(), 1, "failed", "network is unreachable"),
    ],
    ids=["authorized", "already", "ctrl-c", "ctrl-d", "hangup", "wrong-password", "network"],
)
def test_every_exit_writes_its_outcome_and_says_it(
    tmp_path, credentials, capsys, client, ui, exit_code, state, message
):
    stub = client()
    code, payload, said = _run_main(tmp_path, stub, ui(), capsys)
    assert (code, payload["state"]) == (exit_code, state)
    assert message in said
    assert payload["pid"] == os.getpid()
    assert "Traceback" not in said
    assert stub.disconnected or stub.connect_error


def test_an_unscanned_code_exits_expired(tmp_path, credentials, capsys):
    pace = Pace(rotate_after=0.05, min_show=0.05, max_show=5.0, limit=0.3, grace=0.05,
                tick=0.01)
    code = main(["--state-dir", str(tmp_path)], client_factory=lambda _: StubClient(StubQr()),
                ui=ScriptedUi(), pace=pace)
    assert code == 1
    assert json.loads((tmp_path / "auth-status.json").read_text())["state"] == "expired"
    assert "telegram-login again" in capsys.readouterr().err


def test_the_removed_qr_flag_says_what_to_run_instead(tmp_path, capsys):
    assert main(["--qr", "--state-dir", str(tmp_path)]) == 2
    payload = json.loads((tmp_path / "auth-status.json").read_text())
    assert payload["state"] == "failed"
    assert "without flags" in payload["hint"]


# --- --wait --------------------------------------------------------------------------


def _write_later(config, payload, delay):
    def write():
        time.sleep(delay)
        write_status(config, payload)

    threading.Thread(target=write, daemon=True).start()


def test_wait_returns_the_outcome_of_a_login_that_finishes(tmp_path, capsys):
    config = _config(tmp_path)
    _write_later(config, {"state": "authorized", "account": {"id": 42, "name": "Test"}}, 0.2)
    assert login._wait_command(config, 5, poll=0.02) == 0
    assert json.loads(capsys.readouterr().out)["account"]["id"] == 42


def test_wait_ignores_an_outcome_left_by_an_earlier_run(tmp_path, capsys):
    config = _config(tmp_path)
    write_status(config, {"state": "authorized", "account": {"id": 1, "name": "Old"}})
    time.sleep(0.01)
    assert login._wait_command(config, 0.3, poll=0.02) == 3
    assert json.loads(capsys.readouterr().out)["state"] == "timeout"


def _login_holding_the_session(config, outcome, *, pid=None, hold=0.3, after=None):
    """A login as --wait sees it: the session lock held while it runs, the outcome
    written as it lets go. Runs in a thread of this process; with `after`, it starts
    that many seconds later instead of before this returns."""
    from telegram_plugin.client import session_lock

    started = threading.Event()

    def run():
        if after is not None:
            time.sleep(after)
        with session_lock(config.session_path):
            payload = {"state": "waiting", "step": "qr"}
            write_status(config, payload)
            if pid is not None:
                _rewrite_pid(config, pid)
            started.set()
            time.sleep(hold)
        time.sleep(0.05)  # a failing login writes its outcome after releasing the lock
        write_status(config, outcome)

    threading.Thread(target=run, daemon=True).start()
    if after is None:
        assert started.wait(5)


def _rewrite_pid(config, pid):
    payload = json.loads(config.auth_status_path.read_text())
    payload["pid"] = pid
    config.auth_status_path.write_text(json.dumps(payload))


def test_wait_follows_a_login_already_running_when_it_started(tmp_path, capsys):
    config = _config(tmp_path)
    _login_holding_the_session(config, {"state": "failed", "hint": "x"})
    assert login._wait_command(config, 5, poll=0.02, settle=0.2) == 1
    assert json.loads(capsys.readouterr().out)["state"] == "failed"


def test_wait_trusts_the_session_lock_over_a_pid_it_cannot_see(tmp_path, capsys):
    """In a sandbox with its own pid namespace the login's pid looks dead while it runs."""
    config = _config(tmp_path)
    _login_holding_the_session(config, {"state": "authorized", "account": {"id": 42}},
                               pid=2**22 + 12345, after=0.1)
    assert login._wait_command(config, 5, poll=0.02, settle=0.2) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "authorized"


def test_wait_does_not_call_abandoned_the_moment_between_unlock_and_outcome(tmp_path, capsys):
    config = _config(tmp_path)
    _login_holding_the_session(config, {"state": "cancelled", "hint": "x"}, hold=0.05)
    assert login._wait_command(config, 5, poll=0.01, settle=0.2) == 1
    assert json.loads(capsys.readouterr().out)["state"] == "cancelled"


def test_wait_notices_a_login_that_died_without_a_word(tmp_path, capsys):
    config = _config(tmp_path)
    config.state_dir.mkdir(parents=True, exist_ok=True)

    def die_silently():
        time.sleep(0.1)
        payload = {"state": "waiting", "step": "password", "pid": 2**22 + 12345,
                   "written": datetime.now(timezone.utc).isoformat()}
        config.auth_status_path.write_text(json.dumps(payload))

    threading.Thread(target=die_silently, daemon=True).start()
    assert login._wait_command(config, 5, poll=0.02, settle=0.2) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["abandoned"] is True
    assert "terminal" in payload["hint"]


def test_wait_does_not_call_a_stale_unfinished_login_abandoned(tmp_path, capsys):
    config = _config(tmp_path)
    config.state_dir.mkdir(parents=True, exist_ok=True)
    long_ago = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    config.auth_status_path.write_text(json.dumps(
        {"state": "waiting", "pid": 2**22 + 12345, "written": long_ago}))
    assert login._wait_command(config, 0.2, poll=0.02) == 3


# --- --status ------------------------------------------------------------------------


def test_status_answers_where_the_state_directory_cannot_be_written(tmp_path, monkeypatch, capsys):
    """Inside an agent's sandbox chmod and the lock file are refused; status crashed on
    the chmod before it looked at anything."""
    from pathlib import Path

    (tmp_path / ".env").write_text("TELEGRAM_API_ID=1\nTELEGRAM_API_HASH=h\n")
    (tmp_path / "telegram.session").write_bytes(b"")

    def refused(*args, **kwargs):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(Path, "chmod", refused)
    monkeypatch.setattr(Path, "mkdir", refused)
    monkeypatch.setattr(os, "open", refused)
    assert main(["--status", "--state-dir", str(tmp_path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["authorized"] == "unknown"
    assert payload["credentials"] == "present"
    assert "sandbox" in payload["hint"]


def test_status_without_a_session_needs_no_lock(tmp_path, monkeypatch, capsys):
    def refused(*args, **kwargs):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(os, "open", refused)
    assert main(["--status", "--state-dir", str(tmp_path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["authorized"] is False


def test_every_payload_is_stamped_with_time_and_writer(tmp_path):
    config = _config(tmp_path)
    written = write_status(config, {"state": "waiting"})
    assert written["pid"] == os.getpid()
    assert json.loads(config.auth_status_path.read_text())["written"] == written["written"]


def test_the_status_file_is_private(tmp_path):
    config = _config(tmp_path)
    write_status(config, {"state": "waiting"})
    assert stat.S_IMODE(config.auth_status_path.stat().st_mode) == 0o600


def test_the_status_file_never_carries_a_login_link(tmp_path, steps):
    """A tg:// link is useless on the machine showing it, and invites pasting into chat."""
    config = _config(tmp_path)
    import asyncio

    asyncio.run(interactive_login(config, StubClient(StubQr(scan_after=0.02)), ScriptedUi(),
                                  FAST))
    assert "tg://" not in config.auth_status_path.read_text()


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
    """Another Telegram command holds the lock; asking for status must still answer."""
    import multiprocessing as mp
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    session = tmp_path / "telegram.session"
    session.write_bytes(b"")
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
                "TELEGRAM_API_ID": "1",
                "TELEGRAM_API_HASH": "h",
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
    """A sync double for an async API once let a missing `await` through, and only a
    live run found it."""
    import inspect

    from telethon import TelegramClient
    from telethon.tl.custom.qrlogin import QRLogin

    for name in ("wait", "recreate"):
        assert inspect.iscoroutinefunction(getattr(QRLogin, name)), name
        assert inspect.iscoroutinefunction(getattr(StubQr, name)), name
    for name in ("connect", "is_user_authorized", "qr_login", "send_code_request", "sign_in",
                 "get_me"):
        assert inspect.iscoroutinefunction(getattr(TelegramClient, name)), name
        assert inspect.iscoroutinefunction(getattr(StubClient, name)), name


def test_the_double_exposes_what_telethon_exposes_on_a_login_token():
    from telethon.tl.custom.qrlogin import QRLogin

    for name in ("url", "expires"):
        assert isinstance(getattr(QRLogin, name), property), name
        assert isinstance(getattr(StubQr, name), property), name


# --- review findings -----------------------------------------------------------------


async def test_a_scan_being_confirmed_at_the_limit_is_allowed_to_land(tmp_path):
    qr = StubQr(lifetime=5.0, scan_after=0.4)
    pace = Pace(rotate_after=0.05, min_show=0.05, max_show=5.0, limit=0.3, grace=1.0, tick=0.01)
    result = await interactive_login(_config(tmp_path), StubClient(qr), ScriptedUi(), pace)
    assert result["state"] == "authorized"


async def test_p_after_a_confirmed_scan_does_not_abandon_it(tmp_path):
    qr = StubQr(lifetime=0.2, scan_after=0.5, consumed_on_recreate=1)
    ui = ScriptedUi(keys=[(0.35, "p")])
    result = await interactive_login(_config(tmp_path), StubClient(qr), ui, FAST)
    assert result["state"] == "authorized"
    assert not any(event[0] == "ask" for event in ui.events)


async def test_the_p_key_on_a_russian_layout_switches_too(tmp_path):
    ui = ScriptedUi(answers=["+447700900123"], secrets=["12345"], keys=[(0.0, "з")])
    client = StubClient(StubQr())
    await interactive_login(_config(tmp_path), client, ui, FAST)
    assert client.sent == ["447700900123"]


async def test_a_flood_wait_on_resending_an_expired_code_is_reported_as_such(tmp_path):
    client = StubClient(StubQr(), codes=["expired"], flood_on_send=2)
    ui = ScriptedUi(answers=["+447700900123"], secrets=["11111"], keys=[(0.0, "p")])
    with pytest.raises(LoginFailed, match="wait 42 s"):
        await interactive_login(_config(tmp_path), client, ui, FAST)


async def test_telegrams_clock_offset_is_used_for_the_schedule(tmp_path):
    """Telethon measures how far our clock is off; with it, no token is asked for early."""
    qr = StubQr(lifetime=0.4, scan_after=1.3, server_offset=-0.3)
    await interactive_login(_config(tmp_path), StubClient(qr, time_offset=-0.3), ScriptedUi(),
                            FAST)
    assert len(qr.recreations) == qr.tokens - 1, qr.recreations


def test_status_reports_a_session_database_it_cannot_open(tmp_path, monkeypatch, capsys):
    import sqlite3

    (tmp_path / ".env").write_text("TELEGRAM_API_ID=1\nTELEGRAM_API_HASH=h\n")
    (tmp_path / "telegram.session").write_bytes(b"")

    async def unreadable(config):
        raise sqlite3.OperationalError("attempt to write a readonly database")

    monkeypatch.setattr(login, "_report_status", unreadable)
    assert main(["--status", "--state-dir", str(tmp_path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["authorized"] == "unknown"
    assert "readonly database" in payload["hint"]


def test_wait_counts_a_launcher_failure_stamped_in_the_same_second(tmp_path, capsys):
    """The shell launcher can only stamp to the second."""
    config = _config(tmp_path)
    config.state_dir.mkdir(parents=True, exist_ok=True)

    def shell_failure():
        time.sleep(0.1)
        now = datetime.now(timezone.utc).replace(microsecond=0)
        config.auth_status_path.write_text(json.dumps(
            {"state": "failed", "hint": "runtime", "pid": 1, "written": now.isoformat()}))

    threading.Thread(target=shell_failure, daemon=True).start()
    assert login._wait_command(config, 2, poll=0.02) == 1
