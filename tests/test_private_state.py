import os
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from telegram_plugin import login
from telegram_plugin.client import ensure_state_dir, session_lock
from telegram_plugin.config import load_config, load_config_from_environment


def config_at(directory, **extra):
    return load_config({"TELEGRAM_STATE_DIR": str(directory), "TELEGRAM_API_ID": "1",
                        "TELEGRAM_API_HASH": "test-hash", **extra})


@pytest.mark.parametrize("name", [".env", "telegram.session", "telegram.session-journal",
                                  "telegram.session-wal", "telegram.session-shm"])
@pytest.mark.parametrize("link", ["symlink", "hardlink"])
def test_private_files_reject_links_without_changing_the_target(tmp_path, name, link):
    state = tmp_path / "state"
    state.mkdir()
    victim = tmp_path / "unrelated"
    victim.write_text("UNCHANGED")
    victim.chmod(0o644)
    candidate = state / name
    if link == "symlink":
        candidate.symlink_to(victim)
    else:
        os.link(victim, candidate)

    with pytest.raises(OSError):
        ensure_state_dir(config_at(state))

    assert victim.read_text() == "UNCHANGED"
    assert stat.S_IMODE(victim.stat().st_mode) == 0o644


def test_state_directory_symlink_is_refused_without_chmod(tmp_path):
    victim = tmp_path / "unrelated"
    victim.mkdir(mode=0o755)
    state = tmp_path / "state"
    state.symlink_to(victim, target_is_directory=True)
    with pytest.raises(OSError):
        ensure_state_dir(config_at(state))
    assert stat.S_IMODE(victim.stat().st_mode) == 0o755


@pytest.mark.parametrize("link", ["symlink", "hardlink"])
def test_lock_rejects_links_without_changing_target(tmp_path, link):
    victim = tmp_path / "unrelated"
    victim.write_text("UNCHANGED")
    victim.chmod(0o644)
    candidate = tmp_path / "telegram.session.lock"
    if link == "symlink":
        candidate.symlink_to(victim)
    else:
        os.link(victim, candidate)
    with pytest.raises(OSError), session_lock(tmp_path / "telegram.session"):
        pytest.fail("unsafe lock acquired")
    assert stat.S_IMODE(victim.stat().st_mode) == 0o644


def test_dotenv_symlink_is_not_read(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    victim = tmp_path / "unrelated"
    victim.write_text("TELEGRAM_API_HASH=OUTSIDE-CONTENT\n")
    (state / ".env").symlink_to(victim)
    with pytest.raises(OSError):
        load_config_from_environment({"TELEGRAM_STATE_DIR": str(state)})


def test_status_replaces_link_without_writing_or_chmodding_target(tmp_path):
    victim = tmp_path / "unrelated"
    victim.write_text("UNCHANGED")
    victim.chmod(0o644)
    config = config_at(tmp_path / "state")
    config.state_dir.mkdir()
    config.auth_status_path.symlink_to(victim)
    result = login.write_status(config, {"state": "starting"})
    assert victim.read_text() == "UNCHANGED"
    assert stat.S_IMODE(victim.stat().st_mode) == 0o644
    assert not config.auth_status_path.is_symlink()
    assert result["state"] == "starting"
    assert stat.S_IMODE(config.auth_status_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(config.state_dir.stat().st_mode) == 0o700


@pytest.mark.parametrize("name", ["../outside", "/tmp/outside", "nested/file", "a\\b",
                                  ".", "..", "line\nbreak", "null\0byte"])
def test_session_name_cannot_escape_state_directory(tmp_path, name):
    with pytest.raises(ValueError, match="TELEGRAM_SESSION_NAME"):
        config_at(tmp_path, TELEGRAM_SESSION_NAME=name)


def test_real_telethon_session_is_private_at_creation_with_permissive_umask(tmp_path):
    config = config_at(tmp_path / "state")
    ensure_state_dir(config)
    old_mask = os.umask(0)
    try:
        client = login._telethon_client(config)
    finally:
        os.umask(old_mask)
    try:
        assert stat.S_IMODE(config.session_path.stat().st_mode) == 0o600
    finally:
        client.session.close()


def test_valid_dotenv_and_lock_still_work(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / ".env").write_text("TELEGRAM_API_ID=123\nTELEGRAM_API_HASH=test-hash\n")
    config = load_config_from_environment({"TELEGRAM_STATE_DIR": str(state)})
    assert config.api_id == 123 and config.api_hash == "test-hash"
    with session_lock(config.session_path):
        assert stat.S_IMODE((state / "telegram.session.lock").stat().st_mode) == 0o600


def test_config_repr_does_not_disclose_api_hash(tmp_path):
    assert "test-hash" not in repr(config_at(tmp_path))


def test_unsafe_config_is_reported_without_traceback(tmp_path, monkeypatch, capsys):
    state = tmp_path / "state"
    state.mkdir()
    victim = tmp_path / "unrelated"
    victim.write_text("TELEGRAM_API_HASH=OUTSIDE-CONTENT\n")
    (state / ".env").symlink_to(victim)
    monkeypatch.setenv("TELEGRAM_STATE_DIR", str(state))
    assert login.main(["--status"]) == 0
    output = capsys.readouterr()
    assert '"authorized": "unknown"' in output.out
    assert "Traceback" not in output.err and "OUTSIDE-CONTENT" not in output.out


def test_launcher_refuses_state_symlink_before_chmod(tmp_path):
    victim = tmp_path / "unrelated"
    victim.mkdir(mode=0o755)
    state = tmp_path / "state"
    state.symlink_to(victim, target_is_directory=True)
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([str(root / "bin/telegram"), "--help"],
                            env={**os.environ, "TELEGRAM_STATE_DIR": str(state),
                                 "TELEGRAM_PLUGIN_PYTHON": sys.executable},
                            capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode != 0
    assert stat.S_IMODE(victim.stat().st_mode) == 0o755
    assert list(victim.iterdir()) == []


def test_bootstrap_failure_status_does_not_overwrite_symlink_target(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    victim = tmp_path / "unrelated"
    victim.write_text("UNCHANGED")
    victim.chmod(0o644)
    (state / "auth-status.json").symlink_to(victim)
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([str(root / "bin/telegram-login")],
                            env={**os.environ, "TELEGRAM_STATE_DIR": str(state),
                                 "TELEGRAM_PLUGIN_PYTHON": "/nonexistent/python"},
                            capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode == 1
    assert victim.read_text() == "UNCHANGED"
    assert stat.S_IMODE(victim.stat().st_mode) == 0o644
    assert '"state": "failed"' in (state / "auth-status.json").read_text()


def test_sqlite_journal_is_private_during_a_real_transaction(tmp_path):
    config = config_at(tmp_path / "state")
    client = login._telethon_client(config)
    client.session.close()
    old_mask = os.umask(0)
    try:
        connection = sqlite3.connect(config.session_path)
        try:
            connection.execute("UPDATE version SET version=version+1")
            journal = Path(f"{config.session_path}-journal")
            assert journal.is_file()
            assert stat.S_IMODE(journal.stat().st_mode) == 0o600
        finally:
            connection.rollback()
            connection.close()
    finally:
        os.umask(old_mask)


def test_private_reader_does_not_create_or_chmod_state(tmp_path):
    from telegram_plugin.state import read_private_text

    state = tmp_path / "state"
    state.mkdir(mode=0o755)
    dotenv = state / ".env"
    dotenv.write_text("TELEGRAM_API_ID=1\n")
    dotenv.chmod(0o644)
    assert read_private_text(dotenv) == "TELEGRAM_API_ID=1\n"
    assert stat.S_IMODE(state.stat().st_mode) == 0o755
    assert stat.S_IMODE(dotenv.stat().st_mode) == 0o644
    missing = tmp_path / "missing"
    with pytest.raises(FileNotFoundError):
        read_private_text(missing / ".env")
    assert not missing.exists()


def test_permission_change_uses_descriptor_when_filename_is_swapped(tmp_path, monkeypatch):
    from telegram_plugin import state

    candidate = tmp_path / ".env"
    candidate.write_text("SAFE")
    victim = tmp_path / "unrelated"
    victim.write_text("UNCHANGED")
    victim.chmod(0o644)
    original_fstat = os.fstat
    swapped = False

    def swap_after_open(descriptor):
        nonlocal swapped
        info = original_fstat(descriptor)
        if stat.S_ISREG(info.st_mode) and not swapped:
            candidate.unlink()
            candidate.symlink_to(victim)
            swapped = True
        return info

    monkeypatch.setattr(state.os, "fstat", swap_after_open)
    state.tighten_private_file(candidate)
    assert swapped
    assert victim.read_text() == "UNCHANGED"
    assert stat.S_IMODE(victim.stat().st_mode) == 0o644


@pytest.mark.parametrize("kind", ["fifo", "linked_fifo", "linked_file"])
def test_readonly_lock_probe_refuses_unsafe_files_without_blocking(tmp_path, kind):
    config = config_at(tmp_path)
    lock = tmp_path / "telegram.session.lock"
    if kind == "fifo":
        os.mkfifo(lock)
    else:
        target = tmp_path / "target"
        if kind == "linked_fifo":
            os.mkfifo(target)
        else:
            target.write_text("UNCHANGED")
        lock.symlink_to(target)
    result = subprocess.run(
        [sys.executable, "-c", ("import sys; "
         "from telegram_plugin.config import load_config; "
         "from telegram_plugin.login import _session_held; "
         "print(_session_held(load_config({'TELEGRAM_STATE_DIR':sys.argv[1]})))"),
         str(tmp_path)], capture_output=True, text=True, timeout=2, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "None"
    lock.unlink()
    with session_lock(config.session_path):
        assert login._session_held(config) is True
    assert login._session_held(config) is False
