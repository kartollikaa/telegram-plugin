"""What a chat is called in the output.

Telethon marks a supergroup as both a group and a channel, so asking `is_channel` first
labelled every modern group conversation "channel" and left nothing to tell a broadcast
channel from a group chat.
"""

from types import SimpleNamespace

import pytest
from telethon.tl.types import Channel, Chat, User

from telegram_plugin.client import _dialog_type, _entity_type


def _dialog(entity):
    """Mirrors telethon.tl.custom.Dialog, which sets both flags for a supergroup."""
    return SimpleNamespace(
        entity=entity,
        is_user=isinstance(entity, User),
        is_group=isinstance(entity, Chat) or (isinstance(entity, Channel) and entity.megagroup),
        is_channel=isinstance(entity, Channel),
    )


def _channel(**flags):
    return Channel(id=1, title="C", photo=None, date=None, **flags)


BROADCAST = _channel(broadcast=True, megagroup=False)
SUPERGROUP = _channel(broadcast=False, megagroup=True)
# Telegram turns a gigagroup into something that behaves like a channel, and Telethon
# counts it as one — the two tools must not invent a third opinion between them.
GIGAGROUP = _channel(broadcast=True, megagroup=False, gigagroup=True)
LEGACY = Chat(id=1, title="C", photo=None, participants_count=2, date=None, version=1)
PERSON = User(id=1)


def test_the_double_agrees_with_telethon_that_a_supergroup_is_both():
    """The premise of the bug. If Telethon ever stops doing this, say so here."""
    dialog = _dialog(SUPERGROUP)
    assert dialog.is_group and dialog.is_channel


@pytest.mark.parametrize(
    "entity,expected",
    [(BROADCAST, "channel"), (SUPERGROUP, "group"), (GIGAGROUP, "channel"),
     (LEGACY, "group"), (PERSON, "user")],
    ids=["broadcast", "supergroup", "gigagroup", "legacy-group", "user"],
)
def test_dialog_and_entity_agree_on_what_a_chat_is(entity, expected):
    assert _dialog_type(_dialog(entity)) == expected
    assert _entity_type(entity) == expected
