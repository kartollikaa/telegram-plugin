"""`since` and `until` are the only dates a caller supplies, and nothing exercised them.

Python 3.10 is this package's floor and its `fromisoformat` rejects a trailing `Z` — the
one spelling a model reaches for first — so the normalisation is tested against a stand-in
for 3.10's parser rather than only against whatever interpreter happens to run the suite.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from telegram_plugin.config import load_config
from telegram_plugin.server import _moment, build_server
from tests.fakes import FakeGateway


class LikePython310(datetime):
    """3.10 parses no trailing Z, no matter what this interpreter can manage."""

    @classmethod
    def fromisoformat(cls, text):
        if text.endswith(("Z", "z")):
            raise ValueError(f"Invalid isoformat string: {text!r}")
        return datetime.fromisoformat(text)


@pytest.fixture
def python_310(monkeypatch):
    monkeypatch.setattr("telegram_plugin.server.datetime", LikePython310)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("2026-01-31", datetime(2026, 1, 31, tzinfo=timezone.utc)),
        ("2026-01-31T09:00:00", datetime(2026, 1, 31, 9, tzinfo=timezone.utc)),
        ("2026-01-31T09:00:00Z", datetime(2026, 1, 31, 9, tzinfo=timezone.utc)),
        ("2026-01-31T09:00:00z", datetime(2026, 1, 31, 9, tzinfo=timezone.utc)),
        ("2026-01-31T09:00:00+00:00", datetime(2026, 1, 31, 9, tzinfo=timezone.utc)),
        (
            "2026-01-31T09:00:00+03:00",
            datetime(2026, 1, 31, 9, tzinfo=timezone(timedelta(hours=3))),
        ),
    ],
)
def test_accepted_spellings(python_310, text, expected):
    assert _moment(text) == expected


def test_the_z_form_would_fail_on_the_floor_without_the_normalisation(python_310):
    """Positive control: the stand-in really does reject what 3.10 rejects."""
    with pytest.raises(ValueError, match="Invalid isoformat"):
        LikePython310.fromisoformat("2026-01-31T09:00:00Z")


def test_nothing_parses_to_nothing(python_310):
    assert _moment(None) is None
    assert _moment("") is None


def test_a_naive_timestamp_is_read_as_utc(python_310):
    assert _moment("2026-01-31T09:00:00").tzinfo == timezone.utc


async def test_an_unreadable_date_names_the_forms_that_work():
    server = build_server(load_config({"HOME": "/tmp"}), FakeGateway())
    result = await server.call_tool(
        "read_messages", {"chat": "@somechannel", "since": "yesterday"}
    )
    error = json.loads(result.content[0].text)["error"]
    assert "yesterday" in error
    assert "2026-01-31T09:00:00Z" in error
    assert "ValueError" not in error
    assert "Traceback" not in error


async def test_the_schema_tells_the_model_which_spellings_are_accepted():
    """Nothing else steers it away from the one form the floor cannot parse."""
    server = build_server(load_config({"HOME": "/tmp"}), FakeGateway())
    tools = {tool.name: tool for tool in await server.list_tools()}
    properties = tools["read_messages"].input_schema["properties"]
    for field in ("since", "until"):
        assert "ISO 8601" in properties[field]["description"], field
