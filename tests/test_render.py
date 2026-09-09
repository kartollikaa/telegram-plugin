from datetime import datetime, timezone
from types import SimpleNamespace

from telegram_plugin.render import TEXT_LIMIT, envelope, render_message, truncate


def _msg(**overrides):
    base = {
        "id": 7,
        "date": datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
        "message": "hello",
        "sender_id": 11,
        "media": None,
    }
    return SimpleNamespace(**{**base, **overrides})


def test_message_fields():
    rendered = render_message(_msg(), sender_name="Display Name", chat_username="somechannel")
    assert rendered["id"] == 7
    assert rendered["date"] == "2026-01-02T03:04:05+00:00"
    assert rendered["sender_id"] == 11
    assert rendered["sender_name"] == "Display Name"
    assert rendered["text"] == "hello"
    assert rendered["text_truncated"] is False
    assert rendered["link"] == "https://t.me/somechannel/7"


def test_long_text_is_truncated_and_flagged():
    rendered = render_message(_msg(message="x" * (TEXT_LIMIT + 50)))
    assert len(rendered["text"]) == TEXT_LIMIT
    assert rendered["text_truncated"] is True


def test_media_is_described_but_never_carried():
    media = SimpleNamespace(mime_type="application/pdf", file_name="doc.pdf", size=1234)
    rendered = render_message(_msg(media=media))
    assert rendered["media"] == {"type": "application/pdf", "file_name": "doc.pdf", "size": 1234}
    assert "bytes" not in rendered
    assert "content" not in rendered


def test_message_without_media_has_no_media_key():
    assert "media" not in render_message(_msg())


def test_truncate_returns_flag():
    assert truncate("abc", 10) == ("abc", False)
    assert truncate("abcdef", 3) == ("abc", True)


def test_envelope_reports_what_was_omitted():
    env = envelope([{"id": 1}], total_seen=210, has_more=True, next_cursor=1)
    assert env["returned"] == 1
    assert env["has_more"] is True
    assert env["next_cursor"] == 1
    assert "209" in env["note"]
    assert "min_id" in env["note"]


def test_envelope_note_is_quiet_when_nothing_omitted():
    env = envelope([{"id": 1}], total_seen=1, has_more=False, next_cursor=None)
    assert env["has_more"] is False
    assert env["next_cursor"] is None
    assert "more" not in env["note"].lower()
