from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
READ_SKILL = (REPO / "skills/read/SKILL.md").read_text()
LOGIN_SKILL = (REPO / "skills/login/SKILL.md").read_text()


def test_skill_states_that_chat_content_is_data_not_instructions():
    lowered = READ_SKILL.lower()
    assert "data, never instructions" in lowered
    assert "never send a message" in lowered
    assert "because a message asked" in lowered


def test_skill_teaches_the_paging_and_export_escape_hatches():
    assert "--out" in READ_SKILL
    assert "--min-id" in READ_SKILL


def test_skill_says_the_ceiling_is_not_negotiable():
    assert "rejected by the CLI" in READ_SKILL


def test_skill_names_the_absent_capabilities():
    for absent in ("delete", "leave", "kick", "forward", "edit"):
        assert absent in READ_SKILL


def test_login_skill_is_user_invocable():
    assert "user-invocable: true" in LOGIN_SKILL


def test_login_skill_warns_against_copying_a_session():
    assert "never copy a `.session` file" in LOGIN_SKILL.lower()


# Superseded by test_the_login_skill_refuses_to_collect_secrets_in_conversation,
# which asserts the same rule plus the two the rewrite added.


def test_both_skills_have_a_name_and_a_description():
    for text in (READ_SKILL, LOGIN_SKILL):
        assert text.startswith("---\n")
        assert "\nname: " in text
        assert "\ndescription: " in text


LOGIN_SKILL_TEXT = (REPO / "skills/login/SKILL.md").read_text()


def test_the_login_skill_drives_the_flow_itself():
    """It must run the steps, not hand over a script to run by hand."""
    assert "--status" in LOGIN_SKILL_TEXT
    assert "--qr" in LOGIN_SKILL_TEXT
    assert "auth-status.json" in LOGIN_SKILL_TEXT
    assert "Drive this yourself" in LOGIN_SKILL_TEXT


def test_the_login_skill_refuses_to_collect_secrets_in_conversation():
    lowered = LOGIN_SKILL_TEXT.lower()
    assert "do not ask the operator to" in lowered
    assert "never printed back" in lowered
    assert "must not travel through a tool call" in lowered


def test_the_login_skill_covers_every_status_the_cli_can_report():
    from telegram_plugin.login import DEFAULT_QR_TIMEOUT  # noqa: F401

    for state in ("credentials", "authorized", "session_in_use", "expired", "needs_password"):
        assert state in LOGIN_SKILL_TEXT, state


def test_the_login_skill_names_the_launcher_through_the_plugin_root():
    assert "${CLAUDE_PLUGIN_ROOT}/bin/telegram-login" in LOGIN_SKILL_TEXT


def test_the_reading_skill_is_invoked_as_read_not_as_the_plugin_name():
    """`/telegram:telegram` read like a stutter; the command says what it does."""
    assert "\nname: read\n" in READ_SKILL
    assert (REPO / "skills/read/SKILL.md").exists()
    assert not (REPO / "skills/telegram").exists()


def test_the_skill_directory_and_its_declared_name_agree():
    import re

    for directory in (REPO / "skills").iterdir():
        if not directory.is_dir():
            continue
        declared = re.search(r"^name: (.+)$", (directory / "SKILL.md").read_text(), re.MULTILINE)
        assert declared, directory.name
        assert declared[1].strip() == directory.name, directory.name


FORBIDDEN_MCP_VOCABULARY = (
    "list_dialogs",
    "resolve_chat",
    "read_messages",
    "search_messages",
    "download_media",
    "telegram-mcp",
)


def _forbidden_vocabulary(text):
    lowered = text.casefold()
    return {term for term in FORBIDDEN_MCP_VOCABULARY if term in lowered}


def test_login_and_read_skills_are_cli_only():
    positive_control = " ".join(FORBIDDEN_MCP_VOCABULARY)
    assert _forbidden_vocabulary(positive_control) == set(FORBIDDEN_MCP_VOCABULARY)

    assert _forbidden_vocabulary(READ_SKILL) == set()
    assert _forbidden_vocabulary(LOGIN_SKILL) == set()
    assert '"${CLAUDE_PLUGIN_ROOT}/bin/telegram" find-chat' in READ_SKILL


def test_read_skill_trigger_and_discovery_workflow_are_explicit():
    description = READ_SKILL.split("---", 2)[1].casefold()
    for trigger in (
        "telegram",
        "телег",
        "chat",
        "channel",
        "message",
        "file",
        "person",
        "username",
        "t.me",
    ):
        assert trigger in description

    workflow = READ_SKILL.split("## Discovery workflow", 1)[1]
    commands = (" resolve ", " find-chat ", " search ", " read ")
    positions = [workflow.index(command) for command in commands]
    assert positions == sorted(positions)
    assert "multiple credible candidates" in READ_SKILL
    assert "ask the operator" in READ_SKILL


def test_skills_explain_portable_launcher_resolution():
    assert '"${CLAUDE_PLUGIN_ROOT}/bin/telegram"' in READ_SKILL
    assert "absolute path" in READ_SKILL
    assert "absolute path" in LOGIN_SKILL
