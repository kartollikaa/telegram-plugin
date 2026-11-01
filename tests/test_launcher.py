"""The launcher is shell, so these tests actually run it."""

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CLI_LAUNCHER = REPO / "bin" / "telegram"


def _run(state_dir: Path) -> subprocess.CompletedProcess:
    return _run_cli(state_dir, "--help")


def _run_cli(state_dir: Path, *arguments: str) -> subprocess.CompletedProcess:
    environment = {**os.environ, "TELEGRAM_STATE_DIR": str(state_dir)}
    environment.pop("TELEGRAM_PLUGIN_PYTHON", None)
    return subprocess.run(
        [str(CLI_LAUNCHER), *arguments],
        capture_output=True,
        text=True,
        env=environment,
        timeout=600,
        check=False,
    )


def test_cli_launcher_bootstraps_once_without_stdout_noise(tmp_path):
    before = _worktree_state()

    first = _run_cli(tmp_path, "--help")
    second = _run_cli(tmp_path, "--help")

    assert first.returncode == 0
    assert first.stdout.startswith("usage: telegram")
    assert "installing dependencies" not in first.stdout.lower()
    assert "installing dependencies" in first.stderr.lower()
    assert second.returncode == 0
    assert "installing dependencies" not in second.stderr.lower()
    assert (tmp_path / "venv" / "bin" / "python").exists()
    assert _worktree_state() == before


def test_cli_launcher_keeps_all_bootstrap_output_off_stdout(tmp_path):
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_python = fake_bin / "python3"
    fake_python.write_text("#!/bin/sh\necho bootstrap-marker\nexit 1\n")
    fake_python.chmod(0o755)
    state = tmp_path / "state"
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "TELEGRAM_STATE_DIR": str(state),
    }
    environment.pop("TELEGRAM_PLUGIN_PYTHON", None)

    result = subprocess.run(
        [str(CLI_LAUNCHER), "--help"],
        capture_output=True,
        text=True,
        env=environment,
        timeout=120,
        check=False,
    )

    assert result.returncode != 0
    assert result.stdout == ""
    assert "bootstrap-marker" in result.stderr


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


def test_stdout_is_pure_program_output_even_on_the_installing_run(first_run):
    """The install runs on the same invocation that must return parseable output;
    a single pip line on stdout would be indistinguishable from a bad result."""
    _, result, _ = first_run
    assert result.returncode == 0, f"stderr was: {result.stderr[-2000:]}"
    assert result.stdout.startswith("usage: telegram")
    assert "installing dependencies" not in result.stdout.lower()


def test_the_installer_reported_itself_on_stderr(first_run):
    _, result, _ = first_run
    assert "installing dependencies" in result.stderr.lower()


def test_venv_is_built_under_the_state_directory(first_run):
    state, _, _ = first_run
    assert (state / "venv" / "bin" / "python").exists()


def test_no_half_installed_environment_is_left_behind(first_run):
    """The staging directory comes from `mktemp -d "$STATE/venv.XXXXXX"`, so it is
    never literally `venv.tmp`; asserting that name proved nothing."""
    state, _, _ = first_run
    leftovers = [path.name for path in state.glob("venv.*") if path.is_dir()]
    assert leftovers == [], leftovers


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
        [str(CLI_LAUNCHER), "--help"],
        capture_output=True,
        text=True,
        env=environment,
        timeout=120,
        check=False,
    )
    assert result.returncode != 0
    assert "cannot import telethon" in result.stderr
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
    for package in ("telethon",):
        for path in site.glob(f"{package}*"):
            subprocess.run(["rm", "-rf", str(path)], check=True)
    stamp = own_state / "venv" / ".deps-stamp"
    assert stamp.exists(), "the stamp must survive, or this proves nothing"

    again = _run(own_state)
    assert "installing dependencies" in again.stderr.lower()
    assert again.returncode == 0
    assert again.stdout.startswith("usage: telegram")


LOGIN_LAUNCHER = REPO / "bin" / "telegram-login"


def _python_without_segno(tmp_path: Path) -> Path:
    """An interpreter that has Telethon but not the QR library: a runtime built before
    the login started drawing codes."""
    import sys

    blocker = tmp_path / "blocker"
    blocker.mkdir()
    (blocker / "segno.py").write_text("raise ImportError('segno is not installed here')\n")
    wrapper = tmp_path / "python-without-segno"
    wrapper.write_text(
        f'#!/bin/sh\nPYTHONPATH="{blocker}${{PYTHONPATH:+:$PYTHONPATH}}" '
        f'exec "{sys.executable}" "$@"\n'
    )
    wrapper.chmod(0o755)
    return wrapper


def _run_login(state_dir: Path, python: Path, *arguments: str) -> subprocess.CompletedProcess:
    environment = {**os.environ, "TELEGRAM_STATE_DIR": str(state_dir),
                   "TELEGRAM_PLUGIN_PYTHON": str(python)}
    return subprocess.run(
        [str(LOGIN_LAUNCHER), *arguments],
        capture_output=True,
        text=True,
        env=environment,
        timeout=120,
        check=False,
        stdin=subprocess.DEVNULL,
    )


def test_the_login_checks_the_runtime_and_reports_a_missing_piece(tmp_path):
    import json

    state = tmp_path / "state"
    result = _run_login(state, _python_without_segno(tmp_path))
    assert result.returncode == 1
    assert "cannot import telethon or segno" in result.stderr
    assert "could not be installed" in result.stderr
    payload = json.loads((state / "auth-status.json").read_text())
    assert payload["state"] == "failed"
    assert "written" in payload


def test_status_and_wait_skip_the_runtime_check_so_they_answer_in_a_sandbox(tmp_path):
    """bin/telegram chmods the state directory, which a sandbox refuses; the reading
    modes must not go through it."""
    import json

    state = tmp_path / "state"
    python = _python_without_segno(tmp_path)
    status = _run_login(state, python, "--status")
    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)["authorized"] is False
    waited = _run_login(state, python, "--wait", "--timeout", "0")
    assert waited.returncode == 3, waited.stderr
    assert not state.exists(), "a reading mode created the state directory"


def test_a_runtime_from_before_the_qr_library_is_upgraded_by_the_login(own_state):
    """Existing installs: the stamp no longer matches and segno is absent, and the login
    is what the operator runs first."""
    site = next((own_state / "venv" / "lib").glob("python*")) / "site-packages"
    for path in site.glob("segno*"):
        subprocess.run(["rm", "-rf", str(path)], check=True)
    (own_state / "venv" / ".deps-stamp").write_text("stamp-of-1.0.0")
    environment = {**os.environ, "TELEGRAM_STATE_DIR": str(own_state)}
    environment.pop("TELEGRAM_PLUGIN_PYTHON", None)
    result = subprocess.run(
        [str(LOGIN_LAUNCHER), "--help"],
        capture_output=True, text=True, env=environment, timeout=600, check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert "installing dependencies" in result.stderr.lower()
    assert result.stdout.startswith("usage: telegram-login")
    python = own_state / "venv" / "bin" / "python"
    assert subprocess.run([str(python), "-c", "import segno"], check=False).returncode == 0
