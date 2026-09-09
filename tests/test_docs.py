from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
README = (REPO / "README.md").read_text()
ENV_EXAMPLE = (REPO / ".env.example").read_text()


def test_readme_covers_the_four_required_subjects():
    for heading in ("## Install", "## Log in", "## Tools", "## Limits"):
        assert heading in README


def test_readme_says_sending_is_behind_a_flag():
    assert "TELEGRAM_PLUGIN_ALLOW_SEND" in README


def test_readme_warns_against_copying_a_session():
    assert "Never copy a `.session` file" in README


def test_readme_documents_every_environment_variable():
    from telegram_plugin.config import Config  # noqa: F401

    for variable in (
        "TELEGRAM_API_ID",
        "TELEGRAM_API_HASH",
        "TELEGRAM_STATE_DIR",
        "TELEGRAM_SESSION_NAME",
        "TELEGRAM_OUTPUT_ROOT",
        "TELEGRAM_PLUGIN_PYTHON",
        "TELEGRAM_PLUGIN_ALLOW_SEND",
    ):
        assert variable in README, variable
        assert variable in ENV_EXAMPLE, variable


def test_readme_lists_every_tool():
    from telegram_plugin.server import ALL_TOOLS

    for tool in ALL_TOOLS:
        assert f"`{tool}" in README, tool


def test_env_example_carries_names_without_values():
    for line in ENV_EXAMPLE.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            assert stripped.endswith("="), stripped


def test_no_personal_data_in_documentation():
    for text in (README, ENV_EXAMPLE):
        assert "/Users/" not in text
        assert "dmitrijmaksimov" not in text


def test_readme_warns_about_the_cold_first_run():
    # Measured: the first dependency install can outlast a host's MCP startup
    # timeout, so the first session shows no tools. The caveat must not vanish.
    assert "scripts/setup.sh" in README
    assert "may" in README and "no `telegram` tools" in README
