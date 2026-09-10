from pathlib import Path

import pytest

from telegram_plugin.config import load_config
from telegram_plugin.log import config_summary, log

REPO = Path(__file__).resolve().parents[1]


def test_log_writes_to_stderr_and_never_to_stdout(capsys):
    log("connected")
    captured = capsys.readouterr()
    assert "connected" in captured.err
    assert captured.out == ""


@pytest.mark.parametrize("payload", [{"text": "body"}, 42, None, object()])
def test_log_rejects_anything_but_a_string(payload):
    with pytest.raises(TypeError):
        log(payload)


def test_config_summary_omits_the_api_hash():
    planted = "planted-api-hash-value"
    summary = config_summary(load_config({"TELEGRAM_API_HASH": planted, "HOME": "/tmp"}))
    assert planted not in summary
    assert "state" in summary.lower()


# log.py is the server's only writer; login.py is a separate CLI whose job is to
# talk to a human. Every other module runs inside the MCP process, where a stray
# write to stdout corrupts the protocol framing.
STREAM_EXEMPT = {"log.py", "login.py"}


def _server_path_modules():
    return [
        path
        for path in (REPO / "src/telegram_plugin").rglob("*.py")
        if path.name not in STREAM_EXEMPT
    ]


def test_no_module_in_the_server_path_touches_a_stream_directly():
    offenders = {
        path.name: marker
        for path in _server_path_modules()
        for marker in ("stderr", "sys.stdout", "print(")
        if marker in path.read_text()
    }
    assert offenders == {}


def test_the_exemptions_are_the_ones_we_think_they_are():
    names = {path.name for path in (REPO / "src/telegram_plugin").rglob("*.py")}
    assert STREAM_EXEMPT <= names


def test_the_login_cli_keeps_its_secret_prompt_off_the_screen():
    assert "getpass.getpass" in (REPO / "src/telegram_plugin/login.py").read_text()
