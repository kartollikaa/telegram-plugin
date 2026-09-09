import json

import pytest

from telegram_plugin.config import load_config
from telegram_plugin.server import READ_TOOLS, build_server
from tests.fakes import FakeGateway

MINIMAL_ARGUMENTS = {
    "whoami": {},
    "list_dialogs": {},
    "resolve_chat": {"ref": "@somechannel"},
    "read_messages": {"chat": "@somechannel"},
    "search_messages": {"query": "anything"},
    "download_media": {"chat": "@somechannel", "message_id": 1},
}


@pytest.mark.parametrize("tool", READ_TOOLS)
async def test_every_read_tool_explains_how_to_log_in(tool, tmp_path):
    server = build_server(
        load_config({"HOME": str(tmp_path)}), FakeGateway(authorized=False)
    )
    result = await server.call_tool(tool, MINIMAL_ARGUMENTS[tool])
    error = json.loads(result.content[0].text)["error"]
    assert "telegram-login" in error
    assert "Traceback" not in error


async def test_the_send_tool_is_also_guarded(tmp_path):
    server = build_server(
        load_config({"HOME": str(tmp_path), "TELEGRAM_PLUGIN_ALLOW_SEND": "1"}),
        FakeGateway(authorized=False),
    )
    result = await server.call_tool("send_message", {"chat": "@somechannel", "text": "hi"})
    assert "telegram-login" in json.loads(result.content[0].text)["error"]


def test_every_read_tool_is_covered_by_this_file():
    assert set(MINIMAL_ARGUMENTS) == set(READ_TOOLS)
