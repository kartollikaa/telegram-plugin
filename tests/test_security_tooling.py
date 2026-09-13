"""Wiring for scripts/security-check.sh, plus the repository-wide absence guards.

An absence claim is worth nothing without a known-true control, so every matcher
here is first run against a fixture that deliberately contains each forbidden
term. The scan is then run against the real tree."""

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts/security-check.sh"
WORKFLOW = REPO / ".github/workflows/ci.yml"

# The design spec and the implementation plan describe the MCP runtime precisely
# because it was removed; they are archived history, not an active surface.
ARCHIVED_HISTORY = "docs/superpowers/"

# Files whose whole job is to assert that something is absent have to name it.
# Excluding them is unavoidable, so each one is checked below for still being a
# guard — otherwise the exclusion quietly becomes a hole.
ABSENCE_GUARDS = (
    "tests/test_security_tooling.py",
    "tests/test_manifests.py",
    "tests/test_skills.py",
    "tests/test_invite_is_read_only.py",
)

# Never Telethon API, so a match anywhere is a leftover from the MCP runtime.
MCP_RUNTIME = (
    r"\bmcp\b",
    r"mcpServers",
    r"telegram-mcp",
    r"\blist_dialogs\b",
    r"\bresolve_chat\b",
    r"\bread_messages\b",
    r"\bsearch_messages\b",
)

# `download_media` and `send_message` are Telethon's own methods, which the
# gateway calls legitimately. They were ALSO the old tool names, so they are
# forbidden only where they could only mean the tool surface.
MCP_TOOL_NAMES_IN_PROSE = (r"\bdownload_media\b", r"\bsend_message\b")
OPERATOR_FACING = ("README.md", "docs/design.md", ".env.example", "plugin.json")

# Both spellings of every forbidden operation: Telethon's convenience method and
# the raw request it wraps. Watching only the friendly name would miss the one an
# implementer reaches for when the convenience method does not exist — join,
# leave and reaction have no convenience method at all.
FORBIDDEN_SIDE_EFFECTS = (
    r"\bdelete_messages\b",
    r"\bDeleteMessagesRequest\b",
    r"\bedit_message\b",
    r"\bEditMessageRequest\b",
    r"\bforward_messages\b",
    r"\bForwardMessagesRequest\b",
    r"\bJoinChannelRequest\b",
    r"\bLeaveChannelRequest\b",
    r"\bkick_participant\b",
    r"\bedit_permissions\b",
    r"\bEditBannedRequest\b",
    r"\bSendReactionRequest\b",
    r"\bsend_read_acknowledge\b",
    r"\bReadHistoryRequest\b",
)


def _tracked_files() -> list[str]:
    listing = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.split()
    return [
        name
        for name in listing
        if not name.startswith(ARCHIVED_HISTORY) and name not in ABSENCE_GUARDS
    ]


def _hits(patterns, text: str) -> set[str]:
    return {pattern for pattern in patterns if re.search(pattern, text, re.IGNORECASE)}


def _scan(patterns, names=None) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for name in _tracked_files() if names is None else names:
        try:
            text = (REPO / name).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        hit = _hits(patterns, text)
        if hit:
            found[name] = hit
    return found


def test_the_mcp_matcher_finds_a_known_positive():
    fixture = (
        "mcpServers pointing at bin/telegram-mcp, an mcp dependency, and the old "
        "list_dialogs / resolve_chat / read_messages / search_messages tool calls."
    )
    assert _hits(MCP_RUNTIME, fixture) == set(MCP_RUNTIME)


def test_the_prose_tool_name_matcher_finds_a_known_positive():
    assert _hits(
        MCP_TOOL_NAMES_IN_PROSE, "download_media(chat, id) and send_message(chat, text)"
    ) == set(MCP_TOOL_NAMES_IN_PROSE)


def test_the_side_effect_matcher_finds_a_known_positive():
    fixture = (
        "delete_messages DeleteMessagesRequest edit_message EditMessageRequest "
        "forward_messages ForwardMessagesRequest JoinChannelRequest "
        "LeaveChannelRequest kick_participant edit_permissions EditBannedRequest "
        "SendReactionRequest send_read_acknowledge ReadHistoryRequest"
    )
    assert _hits(FORBIDDEN_SIDE_EFFECTS, fixture) == set(FORBIDDEN_SIDE_EFFECTS)


def test_the_matcher_actually_reads_the_repository():
    """Second control: an empty file list would also produce a clean result."""
    tracked = _tracked_files()
    assert len(tracked) > 20
    assert "src/telegram_plugin/cli.py" in tracked


def test_every_excluded_file_is_still_an_absence_guard():
    """The exclusions above are load-bearing. If one stops asserting absence it
    becomes an unwatched place to hide exactly what the sweep looks for."""
    for name in ABSENCE_GUARDS:
        path = REPO / name
        assert path.exists(), name
        text = path.read_text()
        assert re.search(r"assert (not|never)|== \{\}|== set\(\)|not in ", text), name


def test_no_active_mcp_surface_remains():
    found = _scan(MCP_RUNTIME)
    assert found == {}, f"MCP vocabulary still tracked: {found}"


def test_old_tool_names_are_gone_from_operator_facing_files():
    found = _scan(MCP_TOOL_NAMES_IN_PROSE, names=OPERATOR_FACING)
    assert found == {}, f"old tool surface still documented: {found}"


def test_skills_never_name_the_old_tool_surface():
    skills = [str(path.relative_to(REPO)) for path in REPO.glob("skills/*/SKILL.md")]
    assert skills, "the sweep must have something to sweep"
    found = _scan(MCP_RUNTIME + MCP_TOOL_NAMES_IN_PROSE, names=skills)
    assert found == {}, f"a skill still names the old surface: {found}"


def test_forbidden_social_side_effects_are_absent():
    found = _scan(FORBIDDEN_SIDE_EFFECTS)
    assert found == {}, f"a destructive or social operation is present: {found}"


def test_the_script_is_executable():
    assert SCRIPT.exists()
    assert SCRIPT.stat().st_mode & 0o111


def test_the_script_covers_all_four_tools():
    text = SCRIPT.read_text()
    for tool in ("ruff", "bandit", "pip_audit", "shellcheck"):
        assert tool in text, tool


def test_the_script_sweeps_for_secrets_and_personal_paths():
    text = SCRIPT.read_text()
    assert "secrets-in-tree" in text
    assert "secret-files-tracked" in text
    assert "secrets-in-history" in text
    assert "personal-paths" in text


def test_the_script_checks_every_shell_entry_point():
    text = SCRIPT.read_text()
    for entry in ("bin/telegram", "bin/telegram-login", "scripts/*.sh"):
        assert entry in text, entry


def test_ci_runs_the_same_script_rather_than_its_own_copy():
    assert "./scripts/security-check.sh" in WORKFLOW.read_text()


def test_ci_runs_the_test_suite_too():
    assert "python -m pytest" in WORKFLOW.read_text()


def test_ci_fetches_full_history_for_the_history_sweep():
    assert "fetch-depth: 0" in WORKFLOW.read_text()


def test_dependabot_watches_both_ecosystems():
    text = (REPO / ".github/dependabot.yml").read_text()
    assert "package-ecosystem: pip" in text
    assert "package-ecosystem: github-actions" in text


def test_the_allowlist_explains_itself_and_stays_short():
    allowlist = (REPO / ".security-allowlist").read_text()
    entries = [
        line
        for line in allowlist.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert len(entries) <= 3, "every entry is a hole in the history sweep"
    assert "#" in allowlist, "each entry needs a reason above it"
    assert ".security-allowlist" in (REPO / "scripts/security-check.sh").read_text()
