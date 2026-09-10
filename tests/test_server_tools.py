import pytest
from mcp.server.mcpserver.exceptions import ToolError

from telegram_plugin.config import load_config
from telegram_plugin.render import MAX_ITEMS
from telegram_plugin.server import ALL_TOOLS, READ_TOOLS, build_server
from tests.fakes import FakeGateway


def _server(allow_send=False, *, scan_cap=None, **env):
    environment = {"HOME": "/tmp", **env}
    if allow_send:
        environment["TELEGRAM_PLUGIN_ALLOW_SEND"] = "1"
    return build_server(load_config(environment), FakeGateway(scan_cap=scan_cap))


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


async def test_search_in_one_chat_keeps_the_newest_matches_and_pages_backwards():
    server = _server()
    query = {"query": "message", "chat": "@somechannel", "limit": 3}
    first = _payload(await server.call_tool("search_messages", query))
    assert [item["id"] for item in first["items"]] == [10, 11, 12]
    assert first["next_cursor"] == 10
    assert "max_id=10" in first["note"]
    second = _payload(
        await server.call_tool("search_messages", {**query, "max_id": first["next_cursor"]})
    )
    assert [item["id"] for item in second["items"]] == [7, 8, 9]
    assert not {i["id"] for i in first["items"]} & {i["id"] for i in second["items"]}


async def test_a_global_search_offers_no_id_cursor_and_says_why():
    """Ids are only ordered inside one chat: max_id from whichever chat sorted last would
    silently drop every match above it elsewhere."""
    result = _payload(await _server().call_tool("search_messages", {"query": "message", "limit": 3}))
    assert result["has_more"] is True
    assert result["next_cursor"] is None
    assert "max_id=" not in result["note"]
    assert "no id cursor" in result["note"]
    assert "chat=" in result["note"]


async def test_a_global_search_keeps_telegrams_newest_first_order():
    result = _payload(await _server().call_tool("search_messages", {"query": "message", "limit": 3}))
    assert [item["id"] for item in result["items"]] == [12, 11, 10]


async def test_search_never_advertises_a_cursor_it_cannot_accept():
    tools = {tool.name: tool for tool in await _server().list_tools()}
    accepted = set(tools["search_messages"].input_schema["properties"])
    for arguments in ({"query": "message", "limit": 3},
                      {"query": "message", "chat": "@somechannel", "limit": 3}):
        result = _payload(await _server().call_tool("search_messages", arguments))
        named = [field for field in ("min_id", "max_id") if f"{field}=" in result["note"]]
        assert all(field in accepted for field in named)
        assert bool(named) is (result["next_cursor"] is not None), arguments


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


async def test_the_reply_says_how_many_messages_are_left():
    payload = _payload(
        await _server().call_tool("read_messages", {"chat": "@somechannel", "limit": 4})
    )
    assert payload["remaining"] == 8, "12 in the fake, 4 returned"
    assert "8 more available" in payload["note"]


async def test_a_bounded_read_does_not_invent_a_remaining_count():
    payload = _payload(
        await _server().call_tool(
            "read_messages", {"chat": "@somechannel", "limit": 4, "min_id": 2}
        )
    )
    assert "remaining" not in payload
    assert "in total" in payload["note"]


async def test_a_global_search_does_not_claim_a_match_count():
    # Telegram's global search total is not a count: it has been observed reporting
    # more matches for a rare word than for a near-universal substring.
    payload = _payload(
        await _server().call_tool("search_messages", {"query": "message", "limit": 3})
    )
    assert "remaining" not in payload
    assert "not known without scanning" in payload["note"]


async def test_a_search_inside_one_chat_does_report_the_count():
    payload = _payload(
        await _server().call_tool(
            "search_messages", {"query": "message", "chat": "@somechannel", "limit": 3}
        )
    )
    assert payload["remaining"] == 9
    assert "9 more available" in payload["note"]


async def test_a_truncated_read_hands_back_a_cursor_instead_of_claiming_the_end():
    """A scan that stopped at the cap has not reached the end of the range. Reporting
    has_more=false left the agent believing it had everything, with nothing to resume from."""
    payload = _payload(
        await _server(scan_cap=5).call_tool(
            "read_messages", {"chat": "@somechannel", "limit": 50, "media_only": True}
        )
    )
    assert payload["has_more"] is True
    assert payload["next_cursor"] == 5, "the last id looked at, not the last one returned"
    assert "min_id=5" in payload["note"]
    assert "nothing left in this range" not in payload["note"]
    assert "Scanning stopped" in payload["note"]


async def test_a_truncated_read_that_matched_nothing_still_says_where_to_resume():
    payload = _payload(
        await _server(scan_cap=3).call_tool(
            "read_messages", {"chat": "@somechannel", "limit": 50, "media_only": True, "min_id": 4}
        )
    )
    assert payload["returned"] == 0
    assert payload["has_more"] is True
    assert payload["next_cursor"] == 7


async def test_a_complete_export_says_so(tmp_path):
    result = _payload(
        await _server(TELEGRAM_OUTPUT_ROOT=str(tmp_path)).call_tool(
            "read_messages", {"chat": "@somechannel", "out_path": "all.jsonl", "out_limit": 100}
        )
    )
    assert result["complete"] is True
    assert "note" not in result


async def test_an_export_that_stopped_early_is_not_reported_as_a_whole_range(tmp_path):
    """Four healthy-looking numbers over a fraction of the range is the worst answer here."""
    result = _payload(
        await _server(scan_cap=5, TELEGRAM_OUTPUT_ROOT=str(tmp_path)).call_tool(
            "read_messages",
            {"chat": "@somechannel", "out_path": "part.jsonl", "out_limit": 1000},
        )
    )
    assert result["complete"] is False
    assert "prefix of the range" in result["note"]
    assert "min_id=5" in result["note"]
    assert (tmp_path / "part.jsonl").read_text().count("\n") == result["lines"]


async def test_an_export_cut_by_its_own_out_limit_is_also_a_prefix(tmp_path):
    """The scan never stopped early here — out_limit did — and the file is still partial."""
    result = _payload(
        await _server(TELEGRAM_OUTPUT_ROOT=str(tmp_path)).call_tool(
            "read_messages",
            {"chat": "@somechannel", "out_path": "cut.jsonl", "out_limit": 5},
        )
    )
    assert result["complete"] is False
    assert result["lines"] == 5
    assert "min_id=5" in result["note"]


async def test_an_export_of_exactly_the_whole_range_is_complete(tmp_path):
    """Twelve messages into an out_limit of twelve is not a prefix — the off-by-one that
    `>= out_limit` would have called partial."""
    result = _payload(
        await _server(TELEGRAM_OUTPUT_ROOT=str(tmp_path)).call_tool(
            "read_messages",
            {"chat": "@somechannel", "out_path": "exact.jsonl", "out_limit": 12},
        )
    )
    assert result["lines"] == 12
    assert result["complete"] is True


async def test_a_search_export_cut_by_out_limit_keeps_the_newest_and_resumes_below(tmp_path):
    result = _payload(
        await _server(TELEGRAM_OUTPUT_ROOT=str(tmp_path)).call_tool(
            "search_messages",
            {"query": "message", "chat": "@somechannel", "out_path": "s.jsonl", "out_limit": 4},
        )
    )
    assert result["complete"] is False
    assert (result["first_id"], result["last_id"]) == (9, 12), "the newest four, not the oldest"
    assert "max_id=9" in result["note"]


async def test_a_search_export_that_fits_is_complete(tmp_path):
    result = _payload(
        await _server(TELEGRAM_OUTPUT_ROOT=str(tmp_path)).call_tool(
            "search_messages",
            {"query": "message", "chat": "@somechannel", "out_path": "all.jsonl", "out_limit": 12},
        )
    )
    assert result["lines"] == 12
    assert result["complete"] is True


async def test_a_global_search_export_has_no_resume_point_to_name(tmp_path):
    result = _payload(
        await _server(TELEGRAM_OUTPUT_ROOT=str(tmp_path)).call_tool(
            "search_messages", {"query": "message", "out_path": "g.jsonl", "out_limit": 4}
        )
    )
    assert result["complete"] is False
    assert "max_id=" not in result["note"]
    assert "narrow the range" in result["note"]



async def test_a_global_search_says_when_it_ignored_a_max_id():
    """Dropping an argument in silence is how a caller concludes it paged when it did not."""
    result = _payload(
        await _server().call_tool("search_messages", {"query": "message", "max_id": 5, "limit": 3})
    )
    assert "max_id given was ignored" in result["note"]
    assert [item["id"] for item in result["items"]] == [12, 11, 10], "unfiltered, as Telegram gave"


async def test_an_in_chat_search_does_not_claim_to_have_ignored_anything():
    result = _payload(
        await _server().call_tool(
            "search_messages", {"query": "message", "chat": "@somechannel", "max_id": 5, "limit": 3}
        )
    )
    assert "ignored" not in result["note"]
    assert [item["id"] for item in result["items"]] == [2, 3, 4]
