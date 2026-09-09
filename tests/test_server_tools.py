import pytest
from mcp.server.mcpserver.exceptions import ToolError

from telegram_plugin.config import load_config
from telegram_plugin.render import MAX_ITEMS
from telegram_plugin.server import ALL_TOOLS, READ_TOOLS, build_server
from tests.fakes import FakeGateway


def _server(allow_send=False, **env):
    environment = {"HOME": "/tmp", **env}
    if allow_send:
        environment["TELEGRAM_PLUGIN_ALLOW_SEND"] = "1"
    return build_server(load_config(environment), FakeGateway())


async def _names(allow_send=False):
    return sorted(tool.name for tool in await _server(allow_send).list_tools())


async def test_send_is_absent_without_flag():
    assert "send_message" not in await _names()


async def test_send_appears_with_flag():
    assert "send_message" in await _names(allow_send=True)


async def test_tool_surface_is_exactly_the_seven():
    assert await _names(allow_send=True) == sorted(ALL_TOOLS)
    assert await _names() == sorted(READ_TOOLS)
    assert set(ALL_TOOLS) == {
        "whoami",
        "list_dialogs",
        "resolve_chat",
        "read_messages",
        "search_messages",
        "download_media",
        "send_message",
    }


async def test_no_destructive_tool_is_registered():
    banned = ("delete", "leave", "kick", "ban", "forward", "edit", "remove", "block")
    assert [name for name in await _names(allow_send=True) if any(b in name for b in banned)] == []


async def test_limit_ceiling_is_in_the_schema():
    tools = {tool.name: tool for tool in await _server().list_tools()}
    for name in ("list_dialogs", "read_messages", "search_messages"):
        assert tools[name].input_schema["properties"]["limit"]["maximum"] == MAX_ITEMS
        assert tools[name].input_schema["properties"]["limit"]["minimum"] == 1


async def test_over_the_ceiling_is_refused_not_silently_clamped():
    with pytest.raises(ToolError):
        await _server().call_tool("read_messages", {"chat": "@somechannel", "limit": 5000})


async def test_looking_tools_are_annotated_read_only():
    tools = {tool.name: tool for tool in await _server().list_tools()}
    for name in READ_TOOLS:
        if name == "download_media":
            continue
        assert tools[name].annotations.read_only_hint is True, name


async def test_download_is_honest_about_writing_to_disk():
    # It changes nothing in Telegram, but it does create a local file — so not read-only.
    tools = {tool.name: tool for tool in await _server().list_tools()}
    assert tools["download_media"].annotations.read_only_hint is False
    assert tools["download_media"].annotations.destructive_hint is False


async def test_send_is_annotated_as_not_read_only():
    tools = {tool.name: tool for tool in await _server(allow_send=True).list_tools()}
    assert tools["send_message"].annotations.read_only_hint is False
    assert tools["send_message"].annotations.idempotent_hint is False


async def test_read_messages_returns_an_envelope_with_a_cursor():
    result = await _server().call_tool("read_messages", {"chat": "@somechannel", "limit": 4})
    payload = _payload(result)
    assert [item["id"] for item in payload["items"]] == [1, 2, 3, 4]
    assert payload["returned"] == 4
    assert payload["has_more"] is True
    assert payload["next_cursor"] == 4
    assert "min_id=4" in payload["note"]


async def test_search_keeps_the_newest_matches_and_pages_backwards():
    server = _server()
    first = _payload(await server.call_tool("search_messages", {"query": "message", "limit": 3}))
    assert [item["id"] for item in first["items"]] == [10, 11, 12]
    assert first["next_cursor"] == 10
    assert "max_id=10" in first["note"]
    second = _payload(
        await server.call_tool(
            "search_messages", {"query": "message", "limit": 3, "max_id": first["next_cursor"]}
        )
    )
    assert [item["id"] for item in second["items"]] == [7, 8, 9]
    assert not {i["id"] for i in first["items"]} & {i["id"] for i in second["items"]}


async def test_search_never_advertises_a_cursor_it_cannot_accept():
    tools = {tool.name: tool for tool in await _server().list_tools()}
    accepted = set(tools["search_messages"].input_schema["properties"])
    result = _payload(await _server().call_tool("search_messages", {"query": "message", "limit": 3}))
    named = [field for field in ("min_id", "max_id") if f"{field}=" in result["note"]]
    assert named, "the note must name the cursor argument"
    assert all(field in accepted for field in named)


async def test_second_page_continues_from_the_cursor():
    server = _server()
    first = _payload(await server.call_tool("read_messages", {"chat": "@somechannel", "limit": 4}))
    second = _payload(
        await server.call_tool("read_messages", {"chat": "@somechannel", "limit": 4, "min_id": 4})
    )
    assert [item["id"] for item in second["items"]] == [5, 6, 7, 8]
    assert not {i["id"] for i in first["items"]} & {i["id"] for i in second["items"]}


async def test_out_path_returns_a_pointer_instead_of_rows(tmp_path):
    server = _server(TELEGRAM_OUTPUT_ROOT=str(tmp_path))
    result = _payload(
        await server.call_tool(
            "read_messages", {"chat": "@somechannel", "out_path": "dump.jsonl", "out_limit": 100}
        )
    )
    assert result["lines"] == 12
    assert result["first_id"] == 1 and result["last_id"] == 12
    assert "items" not in result
    assert (tmp_path / "dump.jsonl").read_text().count("\n") == 12


async def test_out_path_outside_the_root_is_refused(tmp_path):
    server = _server(TELEGRAM_OUTPUT_ROOT=str(tmp_path))
    result = _payload(
        await server.call_tool("read_messages", {"chat": "@somechannel", "out_path": "../escape.jsonl"})
    )
    assert "Refusing to write" in result["error"]


async def test_unknown_chat_reference_lists_the_accepted_forms():
    result = _payload(await _server().call_tool("read_messages", {"chat": "not a ref!"}))
    assert "t.me" in result["error"]
    assert "Traceback" not in result["error"]


def _payload(result):
    import json

    return json.loads(result.content[0].text)


async def test_sending_names_the_resolved_recipient():
    result = _payload(
        await _server(allow_send=True).call_tool(
            "send_message", {"chat": "@somechannel", "text": "hello"}
        )
    )
    assert result["chat_id"] == -1001
    assert result["chat_title"] == "Alpha"
    assert result["sent_so_far"] == 1


async def test_sending_is_capped_per_process():
    server = _server(allow_send=True, TELEGRAM_PLUGIN_SEND_LIMIT="2")
    for _ in range(2):
        assert "error" not in _payload(
            await server.call_tool("send_message", {"chat": "@somechannel", "text": "hi"})
        )
    refused = _payload(await server.call_tool("send_message", {"chat": "@somechannel", "text": "hi"}))
    assert "ceiling" in refused["error"]


async def test_dialogs_says_when_nothing_matched_rather_than_looking_empty():
    result = _payload(await _server().call_tool("list_dialogs", {"query": "nothing-like-this"}))
    assert result["returned"] == 0
    assert "Nothing matched" in result["note"]
