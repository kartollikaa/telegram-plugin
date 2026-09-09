import multiprocessing as mp
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SOURCE = (REPO / "src/telegram_plugin/login.py").read_text()


def _run(args, state_dir):
    environment = {
        **os.environ,
        "TELEGRAM_STATE_DIR": str(state_dir),
        "PYTHONPATH": str(REPO / "src"),
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
