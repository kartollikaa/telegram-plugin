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
