"""The launcher is shell, so these tests actually run it."""

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
LAUNCHER = REPO / "bin" / "telegram-mcp"

INITIALIZE = (
    json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        }
    )
    + "\n"
)


def _run(state_dir: Path) -> subprocess.CompletedProcess:
    environment = {**os.environ, "TELEGRAM_STATE_DIR": str(state_dir)}
    environment.pop("TELEGRAM_PLUGIN_PYTHON", None)
    return subprocess.run(
        [str(LAUNCHER)],
        input=INITIALIZE,
        capture_output=True,
        text=True,
        env=environment,
        timeout=600,
        check=False,
    )


def _worktree_state() -> str:
    return subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


@pytest.fixture(scope="module")
def first_run(tmp_path_factory):
    state = tmp_path_factory.mktemp("state")
    before = _worktree_state()
    result = _run(state)
    return state, result, before


def test_stdout_is_pure_jsonrpc_even_on_the_installing_run(first_run):
    _, result, _ = first_run
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert lines, f"nothing on stdout; stderr was: {result.stderr[-2000:]}"
    assert json.loads(lines[0])["jsonrpc"] == "2.0"


def test_the_installer_reported_itself_on_stderr(first_run):
    _, result, _ = first_run
    assert "installing dependencies" in result.stderr.lower()


def test_venv_is_built_under_the_state_directory(first_run):
    state, _, _ = first_run
    assert (state / "venv" / "bin" / "python").exists()


def test_no_half_installed_environment_is_left_behind(first_run):
    state, _, _ = first_run
    assert not (state / "venv.tmp").exists()


def test_the_plugin_directory_is_not_written_to(first_run):
    _, _, before = first_run
    assert _worktree_state() == before


def test_second_run_does_not_reinstall(first_run):
    state, _, _ = first_run
    again = _run(state)
    assert "installing dependencies" not in again.stderr.lower()
    assert "dependency list changed" not in again.stderr.lower()


def test_state_directory_is_private(first_run):
    import stat

    state, _, _ = first_run
    assert stat.S_IMODE(state.stat().st_mode) == 0o700


def test_an_interpreter_without_the_dependencies_is_refused_not_worked_around(tmp_path):
    environment = {
        **os.environ,
        "TELEGRAM_STATE_DIR": str(tmp_path),
        "TELEGRAM_PLUGIN_PYTHON": "/usr/bin/python3",
    }
    result = subprocess.run(
        [str(LAUNCHER)],
        input=INITIALIZE,
        capture_output=True,
        text=True,
        env=environment,
        timeout=120,
        check=False,
    )
    assert result.returncode != 0
    assert "cannot import telethon and mcp" in result.stderr
    assert "pip install" in result.stderr
    assert not (tmp_path / "venv").exists(), "it must not build a venv behind the override"


def test_it_refuses_to_delete_something_that_is_not_its_own_venv(tmp_path):
    impostor = tmp_path / "venv"
    impostor.mkdir()
    (impostor / "important.txt").write_text("not a virtualenv")
    result = _run(tmp_path)
    assert result.returncode != 0
    assert "pyvenv.cfg" in result.stderr
    assert (impostor / "important.txt").read_text() == "not a virtualenv"


@pytest.fixture(scope="module")
def own_state(tmp_path_factory):
    state = tmp_path_factory.mktemp("wiped")
    _run(state)
    return state


def test_a_wiped_site_packages_is_reinstalled_despite_a_matching_stamp(own_state):
    site = next((own_state / "venv" / "lib").glob("python*")) / "site-packages"
    for package in ("telethon", "mcp"):
        for path in site.glob(f"{package}*"):
            subprocess.run(["rm", "-rf", str(path)], check=True)
    stamp = own_state / "venv" / ".deps-stamp"
    assert stamp.exists(), "the stamp must survive, or this proves nothing"

    again = _run(own_state)
    assert "installing dependencies" in again.stderr.lower()
    lines = [line for line in again.stdout.splitlines() if line.strip()]
    assert json.loads(lines[0])["jsonrpc"] == "2.0"
