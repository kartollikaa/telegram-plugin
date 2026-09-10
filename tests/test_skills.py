from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TELEGRAM_SKILL = (REPO / "skills/telegram/SKILL.md").read_text()
LOGIN_SKILL = (REPO / "skills/login/SKILL.md").read_text()


def test_skill_states_that_chat_content_is_data_not_instructions():
    lowered = TELEGRAM_SKILL.lower()
    assert "data, never instructions" in lowered
    assert "never send a message" in lowered
    assert "because a message asked" in lowered


def test_skill_teaches_the_paging_and_export_escape_hatches():
    assert "out_path" in TELEGRAM_SKILL
    assert "min_id" in TELEGRAM_SKILL


def test_skill_says_the_ceiling_is_not_negotiable():
    assert "refused by the schema" in TELEGRAM_SKILL


def test_skill_names_the_absent_capabilities():
    for absent in ("delete", "leave", "kick", "forward", "edit"):
        assert absent in TELEGRAM_SKILL


def test_login_skill_is_user_invocable():
    assert "user-invocable: true" in LOGIN_SKILL


def test_login_skill_warns_against_copying_a_session():
    assert "never copy a `.session` file" in LOGIN_SKILL.lower()


# Superseded by test_the_login_skill_refuses_to_collect_secrets_in_conversation,
# which asserts the same rule plus the two the rewrite added.


def test_both_skills_have_a_name_and_a_description():
    for text in (TELEGRAM_SKILL, LOGIN_SKILL):
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
