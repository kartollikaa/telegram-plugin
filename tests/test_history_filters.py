"""The gateway's own filtering and limit logic, against a faithful stand-in for
Telethon's `iter_messages`.

This is deliberately one level below `tests/fakes.py`: the earlier fake modelled
filtering as if Telegram did it server-side, which is exactly what hid a bug
where `since`/`until`/`media_only` were applied after a fixed-size page had
already been fetched. Only `iter_messages` is stubbed, and it is stubbed to
behave the way Telethon documents it: `limit` bounds the API page, and
`reverse=True` yields oldest first.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from telethon.tl.types import MessageReplyHeader

from telegram_plugin.client import TelethonGateway
from telegram_plugin.config import load_config
from telegram_plugin.refs import parse_chat_ref

EPOCH = datetime(2020, 1, 1, tzinfo=timezone.utc)
TOTAL = 1000


def _message(index: int):
    return SimpleNamespace(
        id=index,
        date=EPOCH + timedelta(days=index),
        message=f"message {index}",
        sender_id=1,
        media=SimpleNamespace(mime_type="application/pdf", file_name="d.pdf", size=10)
        if index % 200 == 0
        else None,
        sender=None,
        reply_to=None,
    )


class FakeTelethonClient:
    """Only the two methods the gateway uses, with Telethon's own semantics."""

    def __init__(self, count: int = TOTAL) -> None:
        self.all = [_message(i) for i in range(1, count + 1)]
        self.pages_served = 0

    def iter_messages(self, entity, **kwargs):
        selected = self.all
        min_id = kwargs.get("min_id") or 0
        max_id = kwargs.get("max_id") or 0
        if min_id:
            selected = [m for m in selected if m.id > min_id]
        if max_id:
            selected = [m for m in selected if m.id < max_id]
        if kwargs.get("search"):
            selected = [m for m in selected if kwargs["search"] in m.message]
        if not kwargs.get("reverse"):
            selected = list(reversed(selected))
        limit = kwargs.get("limit")
        served = selected[:limit] if limit else selected
        self.pages_served += 1

        async def generator():
            for message in served:
                yield message

        return generator()

    async def get_messages(self, entity, ids):
        return [next((m for m in self.all if m.id == ids[0]), None)]


@pytest.fixture
def gateway(tmp_path):
    instance = TelethonGateway(load_config({"TELEGRAM_STATE_DIR": str(tmp_path)}))
    client = FakeTelethonClient()
    instance._client = client

    async def entity_of(ref):
        return SimpleNamespace(username="somechannel", id=1)

    instance._entity = entity_of
    return instance, client


REF = parse_chat_ref("@somechannel")


async def test_since_finds_the_messages_that_qualify(gateway):
    instance, _ = gateway
    cutoff = EPOCH + timedelta(days=900)
    batch = await instance.history(REF, limit=50, since=cutoff)
    assert batch.rows, "a `since` in range must not come back empty"
    assert all(row["date"] >= cutoff.isoformat() for row in batch.rows)
    assert len(batch.rows) == 50


async def test_until_stops_at_the_cutoff(gateway):
    instance, _ = gateway
    cutoff = EPOCH + timedelta(days=10)
    batch = await instance.history(REF, limit=50, until=cutoff)
    assert [row["id"] for row in batch.rows] == list(range(1, 11))


async def test_media_only_returns_the_media_that_exists(gateway):
    instance, _ = gateway
    batch = await instance.history(REF, limit=50, media_only=True)
    assert [row["id"] for row in batch.rows] == [200, 400, 600, 800, 1000]
    assert all("media" in row for row in batch.rows)


async def test_the_limit_bounds_accepted_rows_not_the_fetched_page(gateway):
    instance, _ = gateway
    batch = await instance.history(REF, limit=3, media_only=True)
    assert [row["id"] for row in batch.rows] == [200, 400, 600]


async def test_a_filter_that_matches_nothing_reports_the_scan(gateway):
    instance, _ = gateway
    cutoff = EPOCH + timedelta(days=5000)
    batch = await instance.history(REF, limit=50, since=cutoff)
    assert batch.rows == []
    assert batch.scanned > 0, "an empty result must be distinguishable from an empty range"


async def test_a_reply_reaches_the_row_the_gateway_returns(gateway):
    """Rendering is unit-tested; this pins that the gateway hands it the header at all."""
    instance, client = gateway
    client.all[5].reply_to = MessageReplyHeader(reply_to_msg_id=3)
    rows = {row["id"]: row for row in (await instance.history(REF, limit=10)).rows}
    assert rows[6]["reply_to"] == {
        "message_id": 3,
        "link": "https://t.me/somechannel/3",
        "thread_id": None,
    }
    assert "reply_to" not in rows[7]


async def test_the_scan_is_bounded(gateway):
    instance, client = gateway
    client.all = [_message(i) for i in range(1, 100_000)]
    batch = await instance.history(REF, limit=5, media_only=False, from_user="nobody-matches")
    assert batch.scanned <= 20_000
