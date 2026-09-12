import os
import stat
from pathlib import Path

import pytest

import telegram_plugin.paths as paths_module
from telegram_plugin.client import ensure_state_dir
from telegram_plugin.config import load_config
from telegram_plugin.errors import UnsafeInputPath
from telegram_plugin.paths import read_confined_text


def test_state_and_session_modes(tmp_path):
    """The criterion names this node: state directory 0700, session and .env 0600."""
    state = tmp_path / "state"
    state.mkdir()
    session = state / "telegram.session"
    session.write_bytes(b"")
    session.chmod(0o666)
    dotenv = state / ".env"
    dotenv.write_text("TELEGRAM_API_ID=1\n")
    dotenv.chmod(0o644)

    config = load_config({"TELEGRAM_STATE_DIR": str(state)})
    ensure_state_dir(config)

    assert stat.S_IMODE(state.stat().st_mode) == 0o700
    assert stat.S_IMODE(session.stat().st_mode) == 0o600
    assert stat.S_IMODE(dotenv.stat().st_mode) == 0o600


def test_state_dir_is_private(tmp_path):
    config = load_config({"TELEGRAM_STATE_DIR": str(tmp_path / "state")})
    ensure_state_dir(config)
    assert stat.S_IMODE(config.state_dir.stat().st_mode) == 0o700


def test_existing_loose_state_dir_is_tightened(tmp_path):
    state = tmp_path / "state"
    state.mkdir(mode=0o755)
    config = load_config({"TELEGRAM_STATE_DIR": str(state)})
    ensure_state_dir(config)
    assert stat.S_IMODE(state.stat().st_mode) == 0o700


def test_dotenv_is_tightened_when_present(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    dotenv = state / ".env"
    dotenv.write_text("TELEGRAM_API_ID=1\n")
    dotenv.chmod(0o644)
    config = load_config({"TELEGRAM_STATE_DIR": str(state)})
    ensure_state_dir(config)
    assert stat.S_IMODE(dotenv.stat().st_mode) == 0o600


def test_output_root_is_created_private(tmp_path):
    config = load_config({"TELEGRAM_STATE_DIR": str(tmp_path / "state")})
    ensure_state_dir(config)
    assert stat.S_IMODE(config.output_root.stat().st_mode) == 0o700


def test_a_loose_session_file_is_tightened_on_every_start(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    session = state / "telegram.session"
    session.write_bytes(b"")
    session.chmod(0o644)
    config = load_config({"TELEGRAM_STATE_DIR": str(state)})
    ensure_state_dir(config)
    assert stat.S_IMODE(session.stat().st_mode) == 0o600


def test_an_output_root_the_operator_already_uses_keeps_its_own_mode(tmp_path):
    existing = tmp_path / "Downloads"
    existing.mkdir(mode=0o755)
    config = load_config(
        {"TELEGRAM_STATE_DIR": str(tmp_path / "state"), "TELEGRAM_OUTPUT_ROOT": str(existing)}
    )
    ensure_state_dir(config)
    assert stat.S_IMODE(existing.stat().st_mode) == 0o755


def test_an_output_root_we_create_is_private(tmp_path):
    config = load_config(
        {
            "TELEGRAM_STATE_DIR": str(tmp_path / "state"),
            "TELEGRAM_OUTPUT_ROOT": str(tmp_path / "fresh"),
        }
    )
    ensure_state_dir(config)
    assert stat.S_IMODE((tmp_path / "fresh").stat().st_mode) == 0o700


def test_confined_text_reader_resists_a_path_swap(monkeypatch, tmp_path):
    root = tmp_path / "output"
    root.mkdir()
    candidate = root / "message.txt"
    candidate.write_text("SAFE")
    outside = tmp_path / "outside.txt"
    outside.write_text("OUTSIDE-CONTENT")
    original_is_file = Path.is_file
    original_open = os.open
    swapped = False

    def swap_candidate():
        nonlocal swapped
        if swapped:
            return
        candidate.unlink()
        candidate.symlink_to(outside)
        swapped = True

    def racing_is_file(file_path):
        result = original_is_file(file_path)
        if file_path == candidate and result:
            swap_candidate()
        return result

    def racing_open(file_path, flags, mode=0o777, *, dir_fd=None):
        if file_path == candidate.name and dir_fd is not None:
            swap_candidate()
        return original_open(file_path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(Path, "is_file", racing_is_file)
    monkeypatch.setattr(paths_module.os, "open", racing_open)

    with pytest.raises(UnsafeInputPath):
        read_confined_text(candidate.name, root=root)

    assert outside.read_text() == "OUTSIDE-CONTENT"
