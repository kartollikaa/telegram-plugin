import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from telegram_plugin.cli import main
from tests.fakes import FakeGateway

REPO = Path(__file__).resolve().parents[1]


def _environment(tmp_path):
    return {
        "HOME": str(tmp_path),
        "TELEGRAM_OUTPUT_ROOT": str(tmp_path / "output"),
    }


def _invoke(capsys, tmp_path, argv, gateway=None):
    selected = gateway or FakeGateway()
    code = main(
        argv,
        environment=_environment(tmp_path),
        gateway_factory=lambda _config: selected,
    )
    captured = capsys.readouterr()
    return code, captured, selected


def test_help_lists_the_read_command_surface(capsys, tmp_path):
    code, captured, _ = _invoke(capsys, tmp_path, ["--help"])

    assert code == 0
    assert captured.err == ""
    for command in (
        "whoami",
        "dialogs",
        "find-chat",
        "resolve",
        "message",
        "thread",
        "read",
        "search",
        "download",
    ):
        assert command in captured.out


def test_success_is_one_compact_json_line(capsys, tmp_path):
    code, captured, _ = _invoke(capsys, tmp_path, ["whoami"])

    assert code == 0
    assert captured.err == ""
    assert captured.out.endswith("\n")
    assert captured.out.count("\n") == 1
    assert captured.out == json.dumps(json.loads(captured.out), ensure_ascii=False, separators=(",", ":")) + "\n"
    assert json.loads(captured.out)["username"] == "tester"


def test_domain_failure_contract(capsys, tmp_path):
    code, captured, _ = _invoke(capsys, tmp_path, ["message", "@alpha", "404"])

    assert code == 1
    assert captured.err == ""
    assert captured.out.count("\n") == 1
    assert json.loads(captured.out) == {
        "error": {
            "code": "message_not_found",
            "message": "Message 404 was not found in that chat.",
            "retryable": False,
        }
    }


def test_unexpected_failure_uses_the_runtime_error_contract(capsys, tmp_path):
    class ExplodingGateway(FakeGateway):
        async def me(self):
            raise RuntimeError("boom")

    code, captured, _ = _invoke(capsys, tmp_path, ["whoami"], ExplodingGateway())

    assert code == 1
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "error": {
            "code": "unexpected_error",
            "message": "RuntimeError: boom",
            "retryable": False,
        }
    }


@pytest.mark.parametrize(
    ("argv", "diagnostic"),
    [
        (["read"], "required"),
        (["read", "@alpha", "--limit", "201"], "between 1 and 200"),
        (["search", "message", "--limit", "0"], "between 1 and 200"),
        (["search", "message", "--out-limit", "5001"], "between 1 and 5000"),
    ],
)
def test_invalid_usage_contract(capsys, tmp_path, argv, diagnostic):
    code, captured, _ = _invoke(capsys, tmp_path, argv)

    assert code == 2
    assert captured.out == ""
    assert "usage:" in captured.err
    assert diagnostic in captured.err


def test_all_read_commands_dispatch(capsys, tmp_path):
    gateway = FakeGateway()
    gateway.rows[2]["reply_to"] = {"message_id": 10, "link": None, "thread_id": 10}
    gateway.rows[7]["reply_to"] = {"message_id": 10, "link": None, "thread_id": 10}
    commands = [
        (["dialogs", "--query", "alp", "--limit", "1"], lambda value: value["items"][0]["id"] == -1001),
        (["find-chat", "alp", "--limit", "1"], lambda value: value["items"][0]["id"] == -1001),
        (["resolve", "@alpha"], lambda value: value["username"] == "alpha"),
        (["message", "@alpha", "4"], lambda value: value["id"] == 4),
        (["thread", "@alpha", "10", "--limit", "1"], lambda value: value["items"][0]["id"] == 3),
        (["read", "@alpha", "--limit", "2"], lambda value: value["next_cursor"] == 2),
        (["search", "message", "--limit", "2"], lambda value: [row["id"] for row in value["items"]] == [11, 12]),
        (
            ["download", "@alpha", "4", "--dest-dir", "attachments"],
            lambda value: value["path"].endswith("attachments/4.pdf"),
        ),
    ]

    for argv, assertion in commands:
        code, captured, _ = _invoke(capsys, tmp_path, argv, gateway)
        assert code == 0, argv
        assert captured.err == "", argv
        assert assertion(json.loads(captured.out)), argv

    thread_payload = json.loads(
        _invoke(capsys, tmp_path, ["thread", "@alpha", "10", "--limit", "1"], gateway)[
            1
        ].out
    )
    assert thread_payload["has_more"] is True
    assert thread_payload["next_cursor"] is None
    assert "bounded result" in thread_payload["note"]


@pytest.mark.parametrize(
    ("argv", "diagnostic"),
    [
        (["find-chat", ""], "query must not be empty"),
        (["find-chat", "@"], "query must not be empty"),
        (["find-chat", "alpha", "--limit", "51"], "between 1 and 50"),
    ],
)
def test_find_chat_rejects_invalid_usage(capsys, tmp_path, argv, diagnostic):
    code, captured, gateway = _invoke(capsys, tmp_path, argv)

    assert code == 2
    assert captured.out == ""
    assert diagnostic in captured.err
    assert gateway.close_calls == 0


def test_parser_help_does_not_import_telethon():
    guard = """
import builtins
original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name == 'telethon' or name.startswith('telethon.'):
        raise AssertionError(f'unexpected Telethon import: {name}')
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
"""
    environment = {**os.environ, "PYTHONPATH": str(REPO / "src")}
    blocked = subprocess.run(
        [sys.executable, "-c", f"{guard}\nimport telethon\n"],
        cwd=REPO,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    script = (
        f"{guard}\nfrom telegram_plugin.cli import build_parser\n"
        "assert build_parser().parse_args(['dialogs']).command == 'dialogs'\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert blocked.returncode != 0
    assert "unexpected Telethon import: telethon" in blocked.stderr
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        (["--limit", "2"], {"ids": [1, 2], "cursor": 2}),
        (["--limit", "2", "--min-id", "2"], {"ids": [3, 4], "cursor": 4}),
        (["--limit", "2", "--media-only"], {"ids": [4, 8], "cursor": 8}),
    ],
)
def test_read_contract_matrix(capsys, tmp_path, extra, expected):
    code, captured, _ = _invoke(capsys, tmp_path, ["read", "@alpha", *extra])
    payload = json.loads(captured.out)

    assert code == 0
    assert [row["id"] for row in payload["items"]] == expected["ids"]
    assert payload["next_cursor"] == expected["cursor"]


def test_read_contract_matrix_exports_jsonl(capsys, tmp_path):
    code, captured, _ = _invoke(
        capsys,
        tmp_path,
        ["read", "@alpha", "--out", "exports/read.jsonl", "--out-limit", "20"],
    )
    payload = json.loads(captured.out)

    assert code == 0
    assert payload["lines"] == 12
    assert payload["first_id"] == 1
    assert payload["last_id"] == 12
    assert (tmp_path / "output" / "exports" / "read.jsonl").read_text().count("\n") == 12


@pytest.mark.parametrize("chat", [None, "@alpha"])
def test_search_contract_matrix(capsys, tmp_path, chat):
    argv = ["search", "message", "--limit", "3"]
    if chat:
        argv.extend(["--chat", chat])

    code, captured, _ = _invoke(capsys, tmp_path, argv)
    payload = json.loads(captured.out)

    assert code == 0
    assert [row["id"] for row in payload["items"]] == [10, 11, 12]
    assert payload["next_cursor"] == 10
    assert ("remaining" in payload) is bool(chat)


@pytest.mark.parametrize("outcome", ["success", "domain", "unexpected"])
def test_gateway_is_closed_on_every_exit_path(capsys, tmp_path, outcome):
    class LifecycleGateway(FakeGateway):
        def __init__(self):
            super().__init__()
            self.close_calls = 0

        async def me(self):
            if outcome == "unexpected":
                raise RuntimeError("boom")
            return await super().me()

        async def close(self):
            self.close_calls += 1

    gateway = LifecycleGateway()
    argv = ["message", "@alpha", "404"] if outcome == "domain" else ["whoami"]

    code, _, _ = _invoke(capsys, tmp_path, argv, gateway)

    assert code == (0 if outcome == "success" else 1)
    assert gateway.close_calls == 1
