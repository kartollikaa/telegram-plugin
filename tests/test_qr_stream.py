import json
import os
import select
import subprocess
import sys
from pathlib import Path

from telegram_plugin import login, terminal
from tests.login_doubles import ScriptedUi, StubClient, StubQr


def configure(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_API_ID", "1")
    monkeypatch.setenv("TELEGRAM_API_HASH", "h")
    monkeypatch.setattr(terminal, "is_interactive", lambda *_: False)


def test_qr_can_be_shown_in_agent_output_without_a_terminal(tmp_path, monkeypatch, capsys):
    configure(tmp_path, monkeypatch)
    client = StubClient(StubQr(scan_after=0.02))
    code = login.main(["--method", "qr"], client_factory=lambda _: client)
    output = capsys.readouterr()
    assert code == 0
    assert "QR code — new code in" in output.out
    assert "▀" in output.out
    assert "Link Desktop Device" in output.out
    assert "Logged in as Test" in output.out
    assert "\x1b" not in output.out
    assert "tg://" not in output.out
    assert client.disconnected


def test_qr_two_step_login_pauses_for_hidden_input_and_can_resume(tmp_path, monkeypatch, capsys):
    configure(tmp_path, monkeypatch)
    client = StubClient(StubQr(scan_after=0.02, outcome="password"))
    assert login.main(["--method", "qr"], client_factory=lambda _: client) == 4
    payload = json.loads((tmp_path / "auth-status.json").read_text())
    assert payload["state"] == "needs_password"
    assert "--resume" in payload["hint"]
    assert client.signed_in_with == []
    assert client.disconnected
    assert "Password (hidden" not in capsys.readouterr().out

    resumed = StubClient(codes=[])
    ui = ScriptedUi(method=None, secrets=["secret"])
    assert login.main(["--resume"], client_factory=lambda _: resumed, ui=ui) == 0
    assert resumed.signed_in_with == ["password"]
    assert resumed.qr.wait_calls == 0
    assert not ui.shown()
    assert json.loads((tmp_path / "auth-status.json").read_text())["state"] == "authorized"


def test_password_resume_without_hidden_terminal_input_is_refused(tmp_path, monkeypatch, capsys):
    configure(tmp_path, monkeypatch)
    assert login.main(["--resume"]) == 1
    assert "needs a terminal" in capsys.readouterr().err


def test_streamed_qr_frames_refresh_without_terminal_control_codes(capsys):
    ui = terminal.QrOutput()
    ui.show_qr("tg://login?token=first", 30)
    ui.show_qr("tg://login?token=second", 30)
    output = capsys.readouterr().out
    assert output.count("QR code — new code in") == 2
    assert "\x1b" not in output


def test_agent_receives_a_frame_while_the_process_is_still_waiting(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    environment = {**os.environ, "PYTHONPATH": f"{repo / 'src'}:{repo}",
                   "TELEGRAM_API_ID": "1", "TELEGRAM_API_HASH": "h"}
    environment.pop("TELEGRAM_STATE_DIR", None)
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.login_pty_child", str(tmp_path),
         "streamed_qr", "--method", "qr"],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=environment, cwd=repo,
    )
    try:
        assert select.select([process.stdout], [], [], 5)[0], "the first frame was buffered"
        first = os.read(process.stdout.fileno(), 65536).decode()
        assert "QR code" in first
        assert process.poll() is None, "the code arrived only after the process ended"
        remaining, errors = process.communicate(timeout=10)
        output = first + remaining.decode()
        assert process.returncode == 0, errors.decode()
        assert output.count("QR code — new code in") >= 2
        assert "Logged in as Test" in output
        assert "\x1b" not in output
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
