"""Without a login every command must say what to run, not raise. The check runs
through the CLI, because that is the only surface an agent ever sees."""

import json

import pytest

from telegram_plugin.cli import main
from tests.fakes import FakeGateway

READ_INVOCATIONS = {
    "whoami": ["whoami"],
    "dialogs": ["dialogs"],
    "find-chat": ["find-chat", "release"],
    "resolve": ["resolve", "@somechannel"],
    "message": ["message", "@somechannel", "1"],
    "thread": ["thread", "@somechannel", "1"],
    "read": ["read", "@somechannel"],
    "search": ["search", "anything"],
    "download": ["download", "@somechannel", "1"],
}


def _invoke(capsys, tmp_path, argv, environment=None):
    selected = {"HOME": str(tmp_path), "TELEGRAM_OUTPUT_ROOT": str(tmp_path / "output")}
    selected.update(environment or {})
    code = main(
        argv,
        environment=selected,
        gateway_factory=lambda _config: FakeGateway(authorized=False),
    )
    return code, capsys.readouterr()


@pytest.mark.parametrize("command", sorted(READ_INVOCATIONS))
def test_every_read_command_explains_how_to_log_in(command, capsys, tmp_path):
    code, captured = _invoke(capsys, tmp_path, READ_INVOCATIONS[command])

    assert code == 1
    error = json.loads(captured.out)["error"]
    assert error["code"] == "not_authorized"
    assert "telegram-login" in error["message"]
    assert "Traceback" not in captured.out
    assert captured.err == ""


def test_the_send_command_is_also_guarded(capsys, tmp_path):
    code, captured = _invoke(
        capsys,
        tmp_path,
        ["send", "@somechannel", "--text", "hi"],
        {"TELEGRAM_PLUGIN_ALLOW_SEND": "1"},
    )

    assert code == 1
    error = json.loads(captured.out)["error"]
    assert error["code"] == "not_authorized"
    assert "telegram-login" in error["message"]


def test_every_read_command_is_covered_by_this_file():
    from telegram_plugin.cli import build_parser

    commands = next(
        action.choices
        for action in build_parser()._actions
        if getattr(action, "choices", None)
    )
    assert set(READ_INVOCATIONS) == set(commands) - {"send"}
