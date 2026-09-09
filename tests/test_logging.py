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
    planted = "deadbeefdeadbeefdeadbeefdeadbeef"
    summary = config_summary(load_config({"TELEGRAM_API_HASH": planted, "HOME": "/tmp"}))
    assert planted not in summary
    assert "state" in summary.lower()


def test_only_the_log_module_writes_to_stderr():
    offenders = [
        path.name
        for path in (REPO / "src/telegram_plugin").rglob("*.py")
        if path.name != "log.py" and "stderr" in path.read_text()
    ]
    assert offenders == []
