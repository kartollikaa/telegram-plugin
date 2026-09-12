import json

import pytest

from telegram_plugin.application import TelegramApplication
from telegram_plugin.client import DIALOG_SCAN_CAP, Batch
from telegram_plugin.config import load_config
from telegram_plugin.errors import EmptyMessage, NotAuthorized, SendDisabled, UnsafePath
from tests.fakes import FakeGateway


def _application(tmp_path, gateway=None, **environment):
    config = load_config(
        {
            "HOME": str(tmp_path),
            "TELEGRAM_OUTPUT_ROOT": str(tmp_path / "output"),
            **environment,
        }
    )
    return TelegramApplication(config, gateway or FakeGateway())


def _message(message_id, *, media=False):
    result = {
        "id": message_id,
        "date": f"2026-01-01T00:{message_id:02}:00+00:00",
        "sender_id": 100 + (message_id % 3),
        "sender_name": f"Sender {message_id % 3}",
        "text": f"message {message_id}",
        "text_truncated": False,
        "link": f"https://t.me/somechannel/{message_id}",
    }
    if media:
        result["media"] = {
            "type": "application/pdf",
            "file_name": f"{message_id}.pdf",
            "size": 1024,
        }
    return result


async def test_whoami_and_resolve_return_gateway_payloads(tmp_path):
    application = _application(tmp_path)

    assert await application.whoami() == {
        "id": 42,
        "name": "Test Account",
        "username": "tester",
        "is_bot": False,
    }
    assert await application.resolve("@alpha") == {
        "id": -1001,
        "title": "Alpha",
        "type": "channel",
        "username": "alpha",
    }


@pytest.mark.parametrize("operation", ["dialogs", "read", "search", "download", "send"])
async def test_existing_operation_payload_parity(tmp_path, operation):
    application = _application(tmp_path, TELEGRAM_PLUGIN_ALLOW_SEND="1")

    if operation == "dialogs":
        actual = await application.dialogs(query="alp", limit=10)
        expected = {
            "items": [
                {
                    "id": -1001,
                    "title": "Alpha",
                    "type": "channel",
                    "username": "alpha",
                    "unread": 0,
                }
            ],
            "returned": 1,
            "note": "1 chats shown (limit 10). Narrow with query= if the one you want is missing.",
        }
    elif operation == "read":
        actual = await application.read(chat="@alpha", limit=2)
        expected = {
            "items": [_message(1), _message(2)],
            "returned": 2,
            "has_more": True,
            "next_cursor": 2,
            "remaining": 10,
            "note": (
                "2 returned, 10 more available — continue with min_id=2, or pass out_path "
                "to write the whole range to a JSONL file instead of into this conversation."
            ),
        }
    elif operation == "search":
        actual = await application.search(query="message", limit=3)
        expected = {
            "items": [_message(10), _message(11), _message(12, media=True)],
            "returned": 3,
            "has_more": True,
            "next_cursor": 10,
            "note": (
                "3 returned, more available (the count in this range is not known without "
                "scanning it) — continue with max_id=10, or pass out_path to write the whole "
                "range to a JSONL file instead of into this conversation."
            ),
        }
    elif operation == "download":
        actual = await application.download(
            chat="@alpha", message_id=4, dest_dir="attachments"
        )
        expected = {"path": str(tmp_path / "output" / "attachments" / "4.pdf")}
    else:
        actual = await application.send(chat="@alpha", text="hello")
        expected = {
            "message_id": 999,
            "chat_id": -1001,
            "chat_title": "Alpha",
            "reply_to": None,
        }

    assert actual == expected


@pytest.mark.parametrize("operation", ["read", "search"])
async def test_wide_reads_return_jsonl_metadata(tmp_path, operation):
    application = _application(tmp_path)

    if operation == "read":
        result = await application.read(chat="@alpha", out_path="exports/messages.jsonl")
    else:
        result = await application.search(query="message", out_path="exports/messages.jsonl")

    target = tmp_path / "output" / "exports" / "messages.jsonl"
    assert result == {
        "path": str(target),
        "lines": 12,
        "first_id": 1,
        "last_id": 12,
    }
    assert [json.loads(line)["id"] for line in target.read_text().splitlines()] == list(
        range(1, 13)
    )


async def test_send_is_refused_before_gateway_access(tmp_path):
    gateway = FakeGateway()
    application = _application(tmp_path, gateway)

    with pytest.raises(SendDisabled):
        await application.send(chat="not a ref!", text="hello")

    assert gateway.sent == []


@pytest.mark.parametrize("reply_to", [None, 42])
async def test_send_forwards_one_message_and_returns_actual_recipient(
    tmp_path,
    reply_to,
):
    gateway = FakeGateway()
    application = _application(
        tmp_path,
        gateway,
        TELEGRAM_PLUGIN_ALLOW_SEND="1",
    )

    result = await application.send(
        chat="@alpha",
        text="hello",
        reply_to=reply_to,
    )

    assert gateway.sent == [("alpha", "hello", reply_to)]
    assert result == {
        "message_id": 999,
        "chat_id": -1001,
        "chat_title": "Alpha",
        "reply_to": reply_to,
    }


async def test_empty_send_text_is_refused_before_gateway_access(tmp_path):
    gateway = FakeGateway()
    application = _application(
        tmp_path,
        gateway,
        TELEGRAM_PLUGIN_ALLOW_SEND="1",
    )

    with pytest.raises(EmptyMessage):
        await application.send(chat="@alpha", text=" \n\t")

    assert gateway.sent == []


async def test_domain_errors_propagate_to_transport(tmp_path):
    unauthorized = _application(tmp_path, FakeGateway(authorized=False))
    confined = _application(tmp_path)

    with pytest.raises(NotAuthorized):
        await unauthorized.whoami()
    with pytest.raises(UnsafePath):
        await confined.read(chat="@alpha", out_path="../escape.jsonl")


async def test_find_chat_preserves_ambiguous_candidates(tmp_path):
    class AmbiguousGateway(FakeGateway):
        async def dialogs(self, query, limit):
            assert query is None
            assert limit == DIALOG_SCAN_CAP
            return Batch(
                rows=[
                    {
                        "id": -1002,
                        "title": "Mobile Release Archive",
                        "type": "channel",
                        "username": "mobile_release_archive",
                        "unread": 0,
                    },
                    {
                        "id": -1001,
                        "title": "Mobile Release",
                        "type": "channel",
                        "username": "mobile_release",
                        "unread": 2,
                    },
                ],
                scanned=137,
                scan_truncated=True,
            )

    result = await _application(tmp_path, AmbiguousGateway()).find_chat(
        "mobile release",
        limit=10,
    )

    assert [row["id"] for row in result["items"]] == [-1001, -1002]
    assert all({"score", "matched_by"} <= row.keys() for row in result["items"])
    assert result == {
        "items": result["items"],
        "returned": 2,
        "scanned": 137,
        "scan_truncated": True,
        "note": (
            "2 candidate chats found from 137 scanned; scanning stopped at the bounded cap. "
            "Compare score and matched_by, then read a small sample before choosing."
        ),
    }
