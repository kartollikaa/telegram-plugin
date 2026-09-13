"""`since` and `until` are the only dates a caller supplies, and nothing exercised them.

Python 3.10 is this package's floor and its `fromisoformat` rejects a trailing `Z` — the
one spelling a model reaches for first — so the normalisation is tested against a stand-in
for 3.10's parser rather than only against whatever interpreter happens to run the suite.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from telegram_plugin.application import _moment
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
    monkeypatch.setattr("telegram_plugin.application.datetime", LikePython310)


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


def test_an_unreadable_date_names_the_forms_that_work(capsys, tmp_path):
    """The CLI reports it as a branchable code, not a raw ValueError."""
    from telegram_plugin.cli import main

    code = main(
        ["read", "@somechannel", "--since", "yesterday"],
        environment={"HOME": str(tmp_path), "TELEGRAM_OUTPUT_ROOT": str(tmp_path / "o")},
        gateway_factory=lambda _config: FakeGateway(),
    )
    payload = json.loads(capsys.readouterr().out)["error"]
    assert code == 1
    assert payload["code"] == "invalid_timestamp"
    assert "yesterday" in payload["message"]
    assert "ISO 8601" in payload["message"]
    assert "ValueError" not in payload["message"]
    assert "Traceback" not in payload["message"]


def test_the_help_tells_the_model_which_spellings_are_accepted():
    """Nothing else steers it away from the one form the floor cannot parse."""
    from telegram_plugin.cli import build_parser

    commands = next(
        action.choices
        for action in build_parser()._actions
        if getattr(action, "choices", None)
    )
    helps = {
        action.dest: action.help or ""
        for action in commands["read"]._actions
    }
    for field in ("since", "until"):
        assert "ISO 8601" in helps[field], field
