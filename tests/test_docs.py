from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
README = (REPO / "README.md").read_text()
# Prose wraps; matching phrases against the raw text makes tests fail on a reflow.
README_FLAT = " ".join(README.split())
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
        "TELEGRAM_MAX_DOWNLOAD_BYTES",
        "TELEGRAM_PLUGIN_SEND_LIMIT",
        "TELEGRAM_IDLE_TIMEOUT",
        "TELEGRAM_LOCK_WAIT",
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


def test_no_personal_data_in_any_prose_in_the_repository():
    import subprocess

    tracked = subprocess.run(
        ["git", "ls-files", "*.md", "*.example", "*.yml"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert tracked, "the sweep must have something to sweep"
    for name in tracked:
        text = (REPO / name).read_text()
        assert "/Users/" not in text, name
        assert "/home/" not in text, name


def test_the_design_document_documents_every_environment_variable():
    design = (REPO / "docs/design.md").read_text()
    for variable in (
        "TELEGRAM_OUTPUT_ROOT",
        "TELEGRAM_MAX_DOWNLOAD_BYTES",
        "TELEGRAM_PLUGIN_SEND_LIMIT",
        "TELEGRAM_IDLE_TIMEOUT",
        "TELEGRAM_LOCK_WAIT",
    ):
        assert variable in design, variable


def test_readme_warns_about_the_cold_first_run():
    # Measured: the first dependency install can outlast a host's MCP startup
    # timeout, so the first session shows no tools. The caveat must not vanish.
    assert "scripts/setup.sh" in README
    assert "may" in README and "no `telegram` tools" in README


def test_the_readme_leads_with_the_command_not_the_script():
    assert "/telegram:login" in README
    index_command = README.index("/telegram:login")
    index_manual = README.index("The same thing by hand")
    assert index_command < index_manual, "the command must come before the manual path"
    for flag in ("--status", "--qr"):
        assert flag in README, flag


def test_the_readme_explains_the_two_factor_handoff():
    assert "needs_password" in README_FLAT
    assert "must not travel through a tool call" in README_FLAT


def test_every_manifest_agrees_with_the_package_version():
    import json
    import re

    version = re.search(r'^version = "(.*)"$', (REPO / "pyproject.toml").read_text(), re.MULTILINE)[1]
    for directory in (".claude-plugin", ".codex-plugin", ".cursor-plugin"):
        manifest = json.loads((REPO / directory / "plugin.json").read_text())
        assert manifest["version"] == version, directory


def test_the_readme_names_both_commands():
    for command in ("/telegram:read", "/telegram:login"):
        assert command in README, command


def test_the_design_document_describes_every_skill_that_ships():
    design = (REPO / "docs/design.md").read_text()
    for directory in (REPO / "skills").iterdir():
        if directory.is_dir():
            assert f"skills/{directory.name}/SKILL.md" in design, directory.name


def test_the_readme_explains_the_one_account_several_sessions_reality():
    assert "One account, several sessions" in README
    assert "280 ms" in README_FLAT, "the number that justifies releasing the lock"
    for variable in ("TELEGRAM_IDLE_TIMEOUT", "TELEGRAM_LOCK_WAIT"):
        assert variable in README, variable
