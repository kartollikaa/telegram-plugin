"""The gateway's own filtering and limit logic, against a faithful stand-in for
Telethon's `iter_messages`.

This is deliberately one level below `tests/fakes.py`: the earlier fake modelled
filtering as if Telegram did it server-side, which is exactly what hid a bug
where `since`/`until`/`media_only` were applied after a fixed-size page had
already been fetched. Only `iter_messages` is stubbed, and it is stubbed to
behave the way Telethon documents it: `limit` bounds the API page, `from_user`
is applied by Telegram, and `reverse=True` yields oldest first.
"""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from telethon.tl.types import MessageReplyHeader

from telegram_plugin.client import MAX_SCAN_CAP, TelethonGateway, _scan_cap
from telegram_plugin.config import load_config
from telegram_plugin.refs import parse_chat_ref
from tests.telethon_doubles import EPOCH, document_message, plain_message

TOTAL = 1000


def _message(index: int):
    body = {"message": f"message {index}", "sender_id": 1 + (index % 2)}
    if index % 200 == 0:
        return document_message(index, name="d.pdf", size=10, **body,
                                date=EPOCH + timedelta(days=index))
    return plain_message(index, **body, date=EPOCH + timedelta(days=index))


class FakeTelethonClient:
    """Only the two methods the gateway uses, with Telethon's own semantics."""

    def __init__(self, count: int = TOTAL) -> None:
        self.all = [_message(i) for i in range(1, count + 1)]
        self.pages_served = 0
        self.last_kwargs: dict = {}

    def iter_messages(self, entity, **kwargs):
        self.last_kwargs = kwargs
        selected = self.all
        min_id = kwargs.get("min_id") or 0
        max_id = kwargs.get("max_id") or 0
        if min_id:
            selected = [m for m in selected if m.id > min_id]
        if max_id:
            selected = [m for m in selected if m.id < max_id]
        if kwargs.get("search"):
            selected = [m for m in selected if kwargs["search"] in m.message]
        # Telegram applies from_user, so the double must too — pretending otherwise made
        # the loop exit on `limit` and left the scan cap completely unexercised.
        if kwargs.get("from_user") is not None:
            selected = [m for m in selected if m.sender_id == kwargs["from_user"]]
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


async def test_media_metadata_survives_the_whole_gateway_path(gateway):
    instance, _ = gateway
    batch = await instance.history(REF, limit=1, media_only=True)
    assert batch.rows[0]["media"] == {
        "type": "application/pdf",
        "file_name": "d.pdf",
        "size": 10,
    }


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


async def test_from_user_is_handed_to_telegram_rather_than_filtered_here(gateway):
    """It is a server-side filter; applying it locally would burn the scan budget."""
    instance, client = gateway
    batch = await instance.history(REF, limit=5, from_user=2)
    assert client.last_kwargs["from_user"] == 2
    assert [row["sender_id"] for row in batch.rows] == [2] * 5


# The cap exists for the case where the filters reject message after message. Checking it
# only after an accepted row made it unreachable exactly then: measured before the fix, a
# `since` scan over 100 000 messages looked at 99 000 of them, against a cap of 20 000.
@pytest.mark.parametrize(
    "criteria",
    [
        {"since": EPOCH + timedelta(days=99_000)},
        {"media_only": True},
    ],
    ids=["since", "media_only"],
)
async def test_the_scan_is_bounded_when_the_filters_reject_message_after_message(
    gateway, criteria
):
    instance, client = gateway
    client.all = [plain_message(i, message="x", sender_id=1, date=EPOCH + timedelta(days=i))
                  for i in range(1, 100_000)]
    batch = await instance.history(REF, limit=5, **criteria)
    assert batch.scan_truncated is True
    assert batch.scanned == _scan_cap(5)
    assert batch.scanned <= MAX_SCAN_CAP


async def test_a_truncated_scan_says_where_it_stopped(gateway):
    """With nothing accepted there is no returned row to resume from, so the last id the
    scan looked at is the only cursor there is."""
    instance, client = gateway
    client.all = [plain_message(i, message="x", sender_id=1, date=EPOCH + timedelta(days=i))
                  for i in range(1, 100_000)]
    batch = await instance.history(REF, limit=5, media_only=True)
    assert batch.rows == []
    assert batch.last_scanned_id == batch.scanned


async def test_an_untruncated_scan_still_reports_its_last_id(gateway):
    instance, _ = gateway
    batch = await instance.history(REF, limit=3)
    assert batch.scan_truncated is False
    assert batch.last_scanned_id == 3
