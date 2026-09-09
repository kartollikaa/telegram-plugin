import pytest

from telegram_plugin.refs import ChatRef, UnknownChatRef, message_link, parse_chat_ref


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://t.me/somechannel", ChatRef("username", "somechannel", None)),
        ("http://t.me/somechannel", ChatRef("username", "somechannel", None)),
        ("https://telegram.me/somechannel", ChatRef("username", "somechannel", None)),
        ("t.me/somechannel", ChatRef("username", "somechannel", None)),
        ("https://t.me/somechannel/42", ChatRef("username", "somechannel", 42)),
        ("https://t.me/c/1234567890/77", ChatRef("internal_id", 1234567890, 77)),
        ("https://t.me/c/1234567890", ChatRef("internal_id", 1234567890, None)),
        ("https://t.me/+AbCdEf", ChatRef("invite", "AbCdEf", None)),
        ("https://t.me/joinchat/AbCdEf", ChatRef("invite", "AbCdEf", None)),
        ("@somechannel", ChatRef("username", "somechannel", None)),
        ("somechannel", ChatRef("username", "somechannel", None)),
        ("-1001234567890", ChatRef("peer_id", -1001234567890, None)),
        ("777000", ChatRef("peer_id", 777000, None)),
        ("  @somechannel  ", ChatRef("username", "somechannel", None)),
    ],
)
def test_accepted_forms(raw, expected):
    assert parse_chat_ref(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "https://example.com/x", "not a ref!", "@@x", "ab"])
def test_rejected_forms_name_the_accepted_ones(raw):
    with pytest.raises(UnknownChatRef) as excinfo:
        parse_chat_ref(raw)
    assert "t.me" in str(excinfo.value)


def test_message_link_prefers_username():
    assert message_link("somechannel", None, 42) == "https://t.me/somechannel/42"


def test_message_link_falls_back_to_internal_id():
    assert message_link(None, 1234567890, 77) == "https://t.me/c/1234567890/77"


def test_message_link_is_none_without_any_anchor():
    assert message_link(None, None, 77) is None
