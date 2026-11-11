import io
import json
import logging
import os
import re

from telegram_plugin import cli, login
from telegram_plugin.config import load_config
from tests.fakes import FakeGateway
from tests.login_doubles import ScriptedUi

APP_ID = 81234567
APP_HASH = "ab" * 16


def environment(tmp_path):
    return {"HOME": str(tmp_path), "TELEGRAM_STATE_DIR": str(tmp_path / "state"),
            "TELEGRAM_API_ID": str(APP_ID), "TELEGRAM_API_HASH": APP_HASH}


def test_cli_does_not_disclose_credentials_in_errors_or_logs(tmp_path, capsys):
    class FailingGateway(FakeGateway):
        async def me(self):
            logging.getLogger("telethon.test").warning("app %s hash %s", APP_ID, APP_HASH)
            raise RuntimeError(f"app {APP_ID} hash {APP_HASH}")

    stream = io.StringIO()
    logger = logging.getLogger("telethon.test")
    handler = logging.StreamHandler(stream)
    logger.addHandler(handler)
    try:
        result = cli.main(["whoami"], environment=environment(tmp_path),
                          gateway_factory=lambda _: FailingGateway())
    finally:
        logger.removeHandler(handler)
    output = capsys.readouterr()
    assert result == 1
    assert APP_HASH not in output.out + output.err
    assert str(APP_ID) not in output.out + output.err
    assert "[redacted]" in json.loads(output.out)["error"]["message"]
    assert "[redacted]" in stream.getvalue()
    assert APP_HASH not in stream.getvalue() and str(APP_ID) not in stream.getvalue()


def test_cli_redacts_values_reflected_in_an_ordinary_result(tmp_path, capsys):
    class ReflectingGateway(FakeGateway):
        async def me(self):
            return {"id": 42, "name": f"app {APP_ID} hash {APP_HASH}"}

    assert cli.main(["whoami"], environment=environment(tmp_path),
                    gateway_factory=lambda _: ReflectingGateway()) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["id"] == 42
    assert APP_HASH not in output["name"] and str(APP_ID) not in output["name"]


def test_login_failure_and_status_file_do_not_disclose_keys(tmp_path, monkeypatch, capsys):
    for key, value in environment(tmp_path).items():
        monkeypatch.setenv(key, value)

    def factory(_):
        raise RuntimeError(f"app {APP_ID} hash {APP_HASH}")

    assert login.main([], ui=ScriptedUi(), client_factory=factory) == 1
    output = capsys.readouterr()
    text = (tmp_path / "state/auth-status.json").read_text()
    assert APP_HASH not in output.out + output.err + text
    assert str(APP_ID) not in output.out + output.err + text


def test_status_writer_redacts_even_when_called_directly(tmp_path):
    config = load_config(environment(tmp_path))
    result = login.write_status(config, {"state": "failed",
                                        "hint": f"app {APP_ID} hash {APP_HASH}"})
    text = config.auth_status_path.read_text()
    assert APP_HASH not in text and str(APP_ID) not in text
    assert "[redacted]" in result["hint"]


def test_config_repr_contains_neither_application_key(tmp_path):
    text = repr(load_config(environment(tmp_path)))
    assert APP_HASH not in text and str(APP_ID) not in text


def test_logging_traceback_is_redacted_and_factory_restored(tmp_path):
    from telegram_plugin.privacy import private_output

    previous_factory = logging.getLogRecordFactory()
    stream = io.StringIO()
    logger = logging.getLogger("telethon.test")
    handler = logging.StreamHandler(stream)
    logger.addHandler(handler)

    @private_output
    def operation():
        load_config(environment(tmp_path))
        try:
            raise RuntimeError(APP_HASH)
        except RuntimeError:
            logger.exception("app %s", APP_ID)
        logger.warning("ordinary diagnostic remains visible")

    try:
        operation()
    finally:
        logger.removeHandler(handler)
    assert logging.getLogRecordFactory() is previous_factory
    assert "ordinary diagnostic remains visible" in stream.getvalue()
    assert "[redacted]" in stream.getvalue() and "Traceback" in stream.getvalue()
    assert APP_HASH not in stream.getvalue() and str(APP_ID) not in stream.getvalue()


def test_jsonl_export_uses_the_same_redaction_boundary(tmp_path):
    from telegram_plugin.jsonl import write_jsonl
    from telegram_plugin.privacy import private_output

    @private_output
    def operation():
        load_config(environment(tmp_path))
        write_jsonl(tmp_path / "export.jsonl", [{"id": 42, "text": APP_HASH}])

    operation()
    row = json.loads((tmp_path / "export.jsonl").read_text())
    assert row == {"id": 42, "text": "[redacted]"}


def test_numeric_and_embedded_api_ids_are_redacted_without_changing_booleans(tmp_path):
    from telegram_plugin.privacy import private_json, private_output

    @private_output
    def operation():
        load_config(environment(tmp_path))
        return private_json({"id": APP_ID, "text": f"client{APP_ID}client",
                             "ok": True, "other_id": 42, "hash": APP_HASH.upper()})

    result = json.loads(operation())
    assert result == {"id": "[redacted]", "text": "client[redacted]client",
                      "ok": True, "other_id": 42, "hash": "[redacted]"}


def test_hash_redaction_precedes_all_overlapping_id_patterns():
    from telegram_plugin.privacy import Redactor

    redactor = Redactor()
    redactor.register(12345678, "aa" + "12345678bb" * 2 + "12345678dd")
    redactor.register(87654321, "bb" * 16)
    assert redactor.text("aa" + "12345678bb" * 2 + "12345678dd") == "[redacted]"


def test_terminal_redacts_visible_text_without_corrupting_control_sequences():
    from telegram_plugin.privacy import private_output, register_credentials
    from telegram_plugin.terminal import CLEAR_SCREEN, Terminal

    stream = io.StringIO()

    @private_output
    def operation():
        register_credentials(1, "h")
        terminal = Terminal(stdout=stream)
        terminal.show_qr("tg://login?token=synthetic", seconds=1)
        terminal.notice("app 1 hash h")
        terminal.say("app 1 hash h")

    operation()
    output = stream.getvalue()
    assert CLEAR_SCREEN in output
    assert re.search(r"\x1b\[[0-9]+;1H", output)
    assert output.count("app [redacted] hash [redacted]") == 2
    assert "New code in  [redacted]s" in output


def test_terminal_prompts_redact_reflected_credentials(monkeypatch):
    from telegram_plugin.privacy import private_output, register_credentials
    from telegram_plugin.terminal import Terminal

    prompts = []
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or "answer")
    monkeypatch.setattr("telegram_plugin.terminal.getpass.getpass",
                        lambda prompt: prompts.append(prompt) or "secret")

    @private_output
    def operation():
        register_credentials(APP_ID, APP_HASH)
        terminal = Terminal()
        assert terminal.ask(APP_HASH) == "answer"
        assert terminal.ask_secret(f"id{APP_ID}") == "secret"

    operation()
    assert prompts == ["[redacted]", "id[redacted]"]


def test_terminal_footer_counts_wrapping_after_redaction(monkeypatch):
    from telegram_plugin.privacy import private_output, register_credentials
    from telegram_plugin.terminal import Terminal

    monkeypatch.setattr("telegram_plugin.terminal.shutil.get_terminal_size",
                        lambda: os.terminal_size((35, 10)))
    stream = io.StringIO()

    @private_output
    def operation():
        register_credentials(35, "h")
        terminal = Terminal(stdout=stream)
        terminal.show_qr("tg://login?token=synthetic", seconds=25)
        terminal.countdown(24)

    operation()
    output = stream.getvalue()
    visible = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", output)
    before_footer = visible.split("New code in", 1)[0]
    expected_row = sum(max(1, -(-len(line) // 35)) for line in before_footer.splitlines()) + 1
    cursor_row = int(re.findall(r"\x1b\[([0-9]+);1H", output)[-1])
    assert cursor_row == expected_row
