import json
import multiprocessing as mp
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SOURCE = (REPO / "src/telegram_plugin/login.py").read_text()


def _run(args, state_dir, **extra):
    environment = {
        **os.environ,
        "TELEGRAM_STATE_DIR": str(state_dir),
        "PYTHONPATH": str(REPO / "src"),
        **extra,
    }
    for leaked in ("TELEGRAM_API_ID", "TELEGRAM_API_HASH"):
        environment.pop(leaked, None)
    return subprocess.run(
        [sys.executable, "-m", "telegram_plugin.login", *args],
        capture_output=True,
        text=True,
        env=environment,
        timeout=120,
        check=False,
    )


def test_no_secret_bearing_options_exist_in_the_source():
    for forbidden in ("--phone", "--code", "--password", "--2fa", "--token"):
        assert forbidden not in SOURCE


def test_help_offers_only_the_state_directory(tmp_path):
    out = _run(["--help"], tmp_path).stdout
    assert "--state-dir" in out
    for forbidden in ("--phone", "--code", "--password"):
        assert forbidden not in out


def test_the_two_factor_password_is_read_without_echo():
    assert "getpass.getpass" in SOURCE


def test_missing_credentials_are_explained_rather_than_traced(tmp_path):
    result = _run([], tmp_path)
    assert result.returncode == 2
    assert "my.telegram.org" in result.stderr
    assert "Traceback" not in result.stderr


def _hold(path, ready, release):
    from telegram_plugin.client import session_lock

    with session_lock(path):
        ready.set()
        release.wait(30)


def test_login_refuses_while_the_server_holds_the_session(tmp_path):
    session = tmp_path / "telegram.session"
    ready, release = mp.Event(), mp.Event()
    holder = mp.Process(target=_hold, args=(session, ready, release))
    holder.start()
    try:
        assert ready.wait(10)
        result = _run([], tmp_path)
        assert result.returncode == 1
        assert "another process" in result.stderr.lower()
        assert "Traceback" not in result.stderr
    finally:
        release.set()
        holder.join(10)


def _hold_the_session_briefly(path, ready, seconds):
    from telegram_plugin.client import session_lock

    with session_lock(path):
        ready.set()
        time.sleep(seconds)


def test_the_login_waits_for_a_session_the_server_is_about_to_release(tmp_path):
    """The server now lets go once it idles, so refusing instantly would send the
    operator hunting a client that is seconds from releasing."""
    session = tmp_path / "telegram.session"
    ready = mp.Event()
    holder = mp.Process(target=_hold_the_session_briefly, args=(session, ready, 0.7))
    holder.start()
    try:
        assert ready.wait(10)
        environment = {
            **os.environ,
            "TELEGRAM_STATE_DIR": str(tmp_path),
            "TELEGRAM_LOCK_WAIT": "10",
            "PYTHONPATH": str(REPO / "src"),
        }
        for leaked in ("TELEGRAM_API_ID", "TELEGRAM_API_HASH"):
            environment.pop(leaked, None)
        finished = subprocess.run(
            [sys.executable, "-m", "telegram_plugin.login"],
            capture_output=True,
            text=True,
            env=environment,
            timeout=120,
            check=False,
        )
        # 2 is "no credentials" — reached only by getting past the lock.
        # 1 would mean it refused because the session was busy.
        assert finished.returncode == 2, finished.stderr
        assert "my.telegram.org" in finished.stderr
    finally:
        holder.join(10)


def _status_file(state_dir):
    return json.loads((Path(state_dir) / "auth-status.json").read_text())


def test_missing_credentials_reach_the_status_file_not_only_stderr(tmp_path):
    """The /telegram:login skill runs --qr detached with output discarded and polls only
    this file; a failure that never got here left the agent reading a stale one for ever."""
    result = _run(["--qr"], tmp_path)
    assert result.returncode == 2
    payload = _status_file(tmp_path)
    assert payload["state"] == "failed"
    assert "my.telegram.org" in payload["hint"]
    assert "written" in payload


def test_a_held_session_reaches_the_status_file_too(tmp_path):
    session = tmp_path / "telegram.session"
    ready, release = mp.Event(), mp.Event()
    holder = mp.Process(target=_hold, args=(session, ready, release))
    holder.start()
    try:
        assert ready.wait(10)
        # No wait: the point here is the refusal reaching the file, not the waiting.
        assert _run(["--qr"], tmp_path, TELEGRAM_LOCK_WAIT="0").returncode == 1
        payload = _status_file(tmp_path)
        assert payload["state"] == "failed"
        assert "another process" in payload["hint"].lower()
    finally:
        release.set()
        holder.join(10)


def test_a_stale_outcome_is_cleared_before_anyone_polls(tmp_path):
    """An `authorized` left by an earlier run would be read as this run succeeding."""
    (tmp_path / "auth-status.json").write_text(json.dumps({"state": "authorized"}))
    _run(["--qr"], tmp_path)
    assert _status_file(tmp_path)["state"] != "authorized"
