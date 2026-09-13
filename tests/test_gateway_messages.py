from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from telethon.tl.types import User

from telegram_plugin.client import TelethonGateway
from telegram_plugin.config import load_config
from telegram_plugin.errors import MessageNotFound
from telegram_plugin.refs import parse_chat_ref
from tests.fakes import FakeGateway

REF = parse_chat_ref("@alpha")


def _telegram_message(message_id, *, root_id=None):
    reply_to = None
    if root_id is not None:
        reply_to = SimpleNamespace(
            reply_to_msg_id=root_id,
            reply_to_top_id=root_id,
            forum_topic=False,
            reply_to_peer_id=None,
        )
    return SimpleNamespace(
        id=message_id,
        date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        sender_id=42,
        sender=None,
        message=f"message {message_id}",
        reply_to=reply_to,
        media=None,
    )


class MessageClient:
    def __init__(self):
        self.messages = {7: _telegram_message(7)}
        self.thread_rows = [_telegram_message(14, root_id=10), _telegram_message(11, root_id=10)]
        self.message_ids = []
        self.thread_calls = []
        self.send_calls = []

    async def get_messages(self, entity, *, ids):
        self.message_ids.append(ids)
        return self.messages.get(ids)

    async def iter_messages(self, entity, **criteria):
        self.thread_calls.append(criteria)
        ordered = sorted(
            self.thread_rows,
            key=lambda message: message.id,
            reverse=not criteria.get("reverse", False),
        )
        for message in ordered[: criteria["limit"]]:
            yield message

    async def send_message(self, entity, text, *, reply_to):
        self.send_calls.append({"entity": entity, "text": text, "reply_to": reply_to})
        return SimpleNamespace(id=81)


def _telethon_gateway(tmp_path, client):
    gateway = TelethonGateway(load_config({"HOME": str(tmp_path)}))
    entity = User(id=1, first_name="Alpha", username="alpha")

    @asynccontextmanager
    async def session():
        yield client

    async def resolve_entity(ref):
        return entity

    gateway._session = session
    gateway._entity = resolve_entity
    return gateway


async def test_exact_message():
    gateway = FakeGateway()

    assert await gateway.message(REF, 4) == gateway.rows[3]


async def test_missing_exact_message():
    gateway = FakeGateway()

    with pytest.raises(MessageNotFound):
        await gateway.message(REF, 404)


async def test_thread_returns_only_selected_root_replies():
    gateway = FakeGateway()
    gateway.rows[2]["reply_to"] = {"message_id": 10, "link": None, "thread_id": 10}
    gateway.rows[4]["reply_to"] = {"message_id": 99, "link": None, "thread_id": 99}
    gateway.rows[7]["reply_to"] = {"message_id": 10, "link": None, "thread_id": 10}

    batch = await gateway.thread(REF, root_message_id=10, limit=50)

    assert [row["id"] for row in batch.rows] == [3, 8]
    assert all(row["reply_to"]["thread_id"] == 10 for row in batch.rows)


async def test_telethon_message_uses_exact_id_lookup_and_rendering(tmp_path):
    client = MessageClient()
    gateway = _telethon_gateway(tmp_path, client)

    result = await gateway.message(REF, 7)

    assert client.message_ids == [7]
    assert result["id"] == 7
    assert result["link"] == "https://t.me/alpha/7"


async def test_telethon_thread_uses_reply_filter_and_sorts_ascending(tmp_path):
    client = MessageClient()
    client.thread_rows.append(_telegram_message(17, root_id=10))
    gateway = _telethon_gateway(tmp_path, client)

    batch = await gateway.thread(REF, root_message_id=10, limit=2)

    assert client.thread_calls == [
        {"reply_to": 10, "limit": 2, "min_id": 0, "reverse": True}
    ]
    assert [row["id"] for row in batch.rows] == [11, 14]
    assert batch.scanned == 2


@pytest.mark.parametrize("reply_to", [None, 42])
async def test_telethon_send_forwards_reply_and_returns_actual_fields(tmp_path, reply_to):
    client = MessageClient()
    gateway = _telethon_gateway(tmp_path, client)

    result = await gateway.send(REF, "hello; $(not shell)", reply_to=reply_to)

    assert client.send_calls == [
        {
            "entity": client.send_calls[0]["entity"],
            "text": "hello; $(not shell)",
            "reply_to": reply_to,
        }
    ]
    assert result == {
        "message_id": 81,
        "chat_id": 1,
        "chat_title": "Alpha",
        "reply_to": reply_to,
    }
