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


def test_login_skill_does_not_ask_the_agent_to_type_secrets():
    assert "never print their values" in LOGIN_SKILL.lower()


def test_both_skills_have_a_name_and_a_description():
    for text in (TELEGRAM_SKILL, LOGIN_SKILL):
        assert text.startswith("---\n")
        assert "\nname: " in text
        assert "\ndescription: " in text
