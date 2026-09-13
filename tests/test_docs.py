import json
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
README = (REPO / "README.md").read_text()
# Prose wraps; matching phrases against the raw text makes tests fail on a reflow.
README_FLAT = " ".join(README.split())
ENV_EXAMPLE = (REPO / ".env.example").read_text()
DESIGN = (REPO / "docs/design.md").read_text()


def test_readme_covers_the_four_required_subjects():
    for heading in ("## Install", "## Log in", "## Commands", "## Limits"):
        assert heading in README


def test_readme_says_sending_is_behind_a_flag():
    assert "TELEGRAM_PLUGIN_ALLOW_SEND" in README


def test_readme_warns_against_copying_a_session():
    assert "Never copy a `.session` file" in README


def test_readme_documents_every_environment_variable():
    for variable in (
        "TELEGRAM_API_ID",
        "TELEGRAM_API_HASH",
        "TELEGRAM_STATE_DIR",
        "TELEGRAM_SESSION_NAME",
        "TELEGRAM_OUTPUT_ROOT",
        "TELEGRAM_PLUGIN_PYTHON",
        "TELEGRAM_PLUGIN_ALLOW_SEND",
        "TELEGRAM_MAX_DOWNLOAD_BYTES",
        "TELEGRAM_IDLE_TIMEOUT",
        "TELEGRAM_LOCK_WAIT",
    ):
        assert variable in README, variable
        assert variable in ENV_EXAMPLE, variable


def test_readme_lists_every_command_the_cli_actually_has():
    from telegram_plugin.cli import build_parser

    commands = next(
        action.choices
        for action in build_parser()._actions
        if getattr(action, "choices", None)
    )
    for command in commands:
        assert f"`{command}" in README, command


def test_readme_documents_the_exit_code_contract():
    for code in ("`0`", "`1`", "`2`"):
        assert code in README, code
    assert "stdout" in README and "stderr" in README


def test_installation_docs_match_supported_hosts():
    """AC-27. Three real paths are documented, and the one that cannot work is
    named as such rather than left for an operator to discover."""
    assert "claude --plugin-dir" in README_FLAT, "Claude Code install"
    assert "plugin.json" in README, "portable Codex/OpenAI manifest"
    assert "Codex" in README and "OpenAI" in README
    assert "/absolute/path/to/telegram-plugin/bin/telegram" in README, "generic CLI use"

    unsupported = README[README.index("**What does not work:**") :]
    assert "ChatGPT on the web" in unsupported
    assert "no hosted version" in unsupported.lower()


def test_the_readme_does_not_claim_unverified_host_support():
    """The Codex and Cursor compatibility manifests were removed because nobody
    had run them. The claim must not outlive them."""
    for stale in (".codex-plugin", ".cursor-plugin", "Cursor"):
        assert stale not in README, stale


def test_env_example_carries_names_without_values():
    for line in ENV_EXAMPLE.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            assert stripped.endswith("="), stripped


def test_no_personal_data_in_any_prose_in_the_repository():
    tracked = subprocess.run(
        ["git", "ls-files", "*.md", "*.example", "*.yml", "*.json"],
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
    for variable in (
        "TELEGRAM_OUTPUT_ROOT",
        "TELEGRAM_MAX_DOWNLOAD_BYTES",
        "TELEGRAM_IDLE_TIMEOUT",
        "TELEGRAM_LOCK_WAIT",
    ):
        assert variable in DESIGN, variable


def test_obsolete_process_send_quota_is_not_documented():
    for text in (README, ENV_EXAMPLE, DESIGN):
        assert "TELEGRAM_PLUGIN_SEND_LIMIT" not in text


def test_readme_warns_about_the_cold_first_run():
    # The first dependency install is slow enough to look like a hang, and it
    # happens on the same invocation that is supposed to answer.
    assert "scripts/setup.sh" in README
    assert "first dependency install" in README_FLAT


def test_the_readme_leads_with_the_command_not_the_script():
    assert "/telegram:login" in README
    assert README.index("/telegram:login") < README.index("The same thing by hand")
    for flag in ("--status", "--qr"):
        assert flag in README, flag


def test_the_readme_explains_the_two_factor_handoff():
    assert "needs_password" in README_FLAT
    assert "must not travel through a tool call" in README_FLAT


def test_every_manifest_agrees_with_the_package_version():
    version = re.search(
        r'^version = "(.*)"$', (REPO / "pyproject.toml").read_text(), re.MULTILINE
    )[1]
    for name in ("plugin.json", ".claude-plugin/plugin.json"):
        manifest = json.loads((REPO / name).read_text())
        assert manifest["version"] == version, name


def test_the_readme_names_every_skill():
    for command in ("/telegram:read", "/telegram:login", "/telegram:send"):
        assert command in README, command


def test_the_design_document_describes_every_skill_that_ships():
    for directory in (REPO / "skills").iterdir():
        if directory.is_dir():
            assert f"skills/{directory.name}/SKILL.md" in DESIGN, directory.name


def test_the_readme_explains_the_one_account_several_sessions_reality():
    assert "One account, several sessions" in README
    assert "reconnecting is not a fresh handshake" in README_FLAT
    for variable in ("TELEGRAM_IDLE_TIMEOUT", "TELEGRAM_LOCK_WAIT"):
        assert variable in README, variable


def test_every_cli_flag_is_documented_in_the_readme():
    """The README is the operator's reference, so a flag has to be there — not only
    in a skill, which is the agent's. Checking "documented somewhere" would have
    passed on `send --reply-to`, which lived only in the send skill."""
    from telegram_plugin.cli import build_parser

    commands = next(
        action.choices
        for action in build_parser()._actions
        if getattr(action, "choices", None)
    )
    undocumented = {
        option
        for parser in commands.values()
        for action in parser._actions
        for option in action.option_strings
        if option not in ("-h", "--help") and option not in README
    }
    assert undocumented == set(), undocumented


def test_every_error_code_the_code_can_raise_is_documented():
    import inspect

    from telegram_plugin import errors, refs

    codes = {
        cls.code
        for module in (errors, refs)
        for _, cls in inspect.getmembers(module, inspect.isclass)
        if issubclass(cls, errors.TelegramPluginError) and hasattr(cls, "code")
    }
    missing = {code for code in codes if f"`{code}`" not in README}
    assert missing == set(), missing
