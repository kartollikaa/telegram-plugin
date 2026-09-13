"""Wiring for scripts/security-check.sh, plus the repository-wide absence guards.

An absence claim is worth nothing without a known-true control, so every matcher
here is first run against a fixture that deliberately contains each forbidden
term. The scan is then run against the real tree."""

import ast
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts/security-check.sh"
WORKFLOW = REPO / ".github/workflows/ci.yml"

# The design spec and the implementation plan describe the MCP runtime precisely
# because it was removed; they are archived history, not an active surface.
# Exempting a whole prefix would let a new file be dropped in beside these two and
# inherit the exemption. Only the archived design record itself is exempt, by name.
ARCHIVED_HISTORY = (
    "docs/superpowers/specs/2026-09-12-cli-first-agent-plugin-design.md",
    "docs/superpowers/plans/2026-09-12-cli-first-agent-plugin.md",
)

# Files whose whole job is to assert that something is absent have to name it.
# Excluding them is unavoidable, so each is checked below for still being a guard.
ABSENCE_GUARDS = (
    "tests/test_security_tooling.py",
    "tests/test_manifests.py",
    "tests/test_skills.py",
    "tests/test_invite_is_read_only.py",
)

# Never Telethon API, so a match anywhere is a leftover from the MCP runtime.
# `mcp` is matched without a trailing word boundary because `_` is a word
# character: \bmcp\b would sail past fastmcp and mcp_server_lib.
MCP_RUNTIME = (
    r"(?<![a-z0-9_])mcp(?![a-z0-9])",
    r"mcp[_a-z]*server",
    r"fastmcp",
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
OPERATOR_FACING = (
    "README.md",
    "docs/design.md",
    ".env.example",
    "plugin.json",
    ".claude-plugin/plugin.json",
)

# Every spelling of every forbidden operation that Telethon 1.x actually offers:
# the convenience method, the Message shortcut, and the raw request. Watching one
# spelling is what let an earlier version of this guard pass a command that left
# chats -- `delete_dialog` is the convenience method for leaving, so "leave has no
# convenience method" was simply wrong.
FORBIDDEN_SIDE_EFFECTS = (
    r"\bdelete_messages\b",
    r"\bdelete_dialog\b",
    r"\bDeleteMessagesRequest\b",
    r"\bDeleteHistoryRequest\b",
    r"\bDeleteChatUserRequest\b",
    r"\bedit_message\b",
    r"\bEditMessageRequest\b",
    r"\bforward_messages\b",
    r"\bForwardMessagesRequest\b",
    r"\bforward_to\b",
    r"\bJoinChannelRequest\b",
    r"\bLeaveChannelRequest\b",
    r"\bkick_participant\b",
    r"\bedit_permissions\b",
    r"\bEditBannedRequest\b",
    r"\bBlockRequest\b",
    r"\bpin_message\b",
    r"\bunpin_message\b",
    r"\bUpdatePinnedMessageRequest\b",
    r"\bSendReactionRequest\b",
    r"\bsend_read_acknowledge\b",
    r"\bmark_read\b",
    r"\bMarkDialogUnreadRequest\b",
    r"\bReadHistoryRequest\b",
    r"\bsend_file\b",
)

# A denylist only ever catches a spelling it already knows, and Telethon offers
# hundreds it does not. These three allowlists are the actual backstop: the whole
# agent-reachable surface has to match, so a new capability fails whether it
# arrives as a new command, a new flag on an old command, or a new Telegram call
# behind either.
EXPECTED_COMMANDS: dict[str, tuple[list[str], list[str]]] = {
    "whoami": ([], []),
    "dialogs": (["--limit", "--query"], []),
    "find-chat": (["--limit"], ["query"]),
    "resolve": ([], ["chat"]),
    "message": ([], ["chat", "message_id"]),
    "thread": (["--limit"], ["chat", "root_message_id"]),
    "read": (
        [
            "--from-user",
            "--limit",
            "--max-id",
            "--media-only",
            "--min-id",
            "--out",
            "--out-limit",
            "--since",
            "--until",
        ],
        ["chat"],
    ),
    "search": (["--chat", "--limit", "--max-id", "--out", "--out-limit"], ["query"]),
    "download": (["--dest-dir"], ["chat", "message_id"]),
    "send": (["--reply-to", "--text", "--text-file"], ["chat"]),
}

# Every Telethon method the agent-facing gateway may call, and every raw request
# it may send. Read off the code and frozen: `log_out`, `delete_dialog`,
# `SendMediaRequest` and the rest fail here no matter which command reaches them.
ALLOWED_GATEWAY_CALLS = frozenset(
    {
        "connect",
        "disconnect",
        "download_media",
        "get_entity",
        "get_me",
        "get_messages",
        "is_user_authorized",
        "iter_dialogs",
        "iter_messages",
        "send_message",
    }
)
ALLOWED_RAW_REQUESTS = frozenset({"CheckChatInviteRequest"})

# The login program runs out of process, in front of a human, and is the only
# place allowed to authenticate. Keeping its surface separate is the point: these
# appearing in the gateway would mean an agent-reachable login.
ALLOWED_LOGIN_ONLY_CALLS = frozenset({"start", "qr_login", "sign_in", "log_out"})


def _tracked_files() -> list[str]:
    """NUL-separated: splitting on whitespace turns `legacy bridge.py` into two
    paths that do not exist, and _scan's OSError guard then swallows both."""
    listing = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.split("\0")
    return [
        name
        for name in listing
        if name and name not in ARCHIVED_HISTORY and name not in ABSENCE_GUARDS
    ]


def _hits(patterns, text: str) -> set[str]:
    return {pattern for pattern in patterns if re.search(pattern, text, re.IGNORECASE)}


def _read_for_scan(path) -> str:
    """latin-1 decodes any byte sequence, so nothing is skipped for being
    undecodable. Swallowing UnicodeDecodeError here used to mean a latin-1 source
    full of forbidden vocabulary passed every guard in this file."""
    return path.read_bytes().decode("latin-1")


def _scan(patterns, names=None) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for name in _tracked_files() if names is None else names:
        path = REPO / name
        if not path.is_file():
            continue
        hit = _hits(patterns, _read_for_scan(path))
        if hit:
            found[name] = hit
    return found


def test_the_mcp_matcher_finds_a_known_positive():
    fixture = (
        "mcpServers pointing at bin/telegram-mcp, a bare mcp dependency, fastmcp, "
        "an mcp_server_lib import, and the old list_dialogs / resolve_chat / "
        "read_messages / search_messages tool calls."
    )
    assert _hits(MCP_RUNTIME, fixture) == set(MCP_RUNTIME)


def test_the_prose_tool_name_matcher_finds_a_known_positive():
    assert _hits(
        MCP_TOOL_NAMES_IN_PROSE, "download_media(chat, id) and send_message(chat, text)"
    ) == set(MCP_TOOL_NAMES_IN_PROSE)


def test_the_side_effect_matcher_finds_a_known_positive():
    fixture = (
        "delete_messages delete_dialog DeleteMessagesRequest DeleteHistoryRequest "
        "DeleteChatUserRequest edit_message EditMessageRequest forward_messages "
        "ForwardMessagesRequest forward_to JoinChannelRequest LeaveChannelRequest "
        "kick_participant edit_permissions EditBannedRequest BlockRequest "
        "pin_message unpin_message UpdatePinnedMessageRequest SendReactionRequest "
        "send_read_acknowledge mark_read MarkDialogUnreadRequest ReadHistoryRequest "
        "send_file"
    )
    missed = set(FORBIDDEN_SIDE_EFFECTS) - _hits(FORBIDDEN_SIDE_EFFECTS, fixture)
    assert missed == set(), f"patterns that match nothing are dead weight: {missed}"


def test_the_matcher_actually_reads_the_repository():
    """Second control: an empty file list would also produce a clean result."""
    tracked = _tracked_files()
    assert len(tracked) > 20
    assert "src/telegram_plugin/cli.py" in tracked


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


def _command_surface() -> dict[str, tuple[list[str], list[str]]]:
    from telegram_plugin.cli import build_parser

    commands = next(
        action.choices
        for action in build_parser()._actions
        if getattr(action, "choices", None)
    )
    surface = {}
    for name, parser in commands.items():
        options, positionals = set(), []
        for action in parser._actions:
            if action.option_strings:
                options.update(o for o in action.option_strings if o not in ("-h", "--help"))
            elif action.dest != "help":
                positionals.append(action.dest)
        surface[name] = (sorted(options), positionals)
    return surface


def test_the_cli_exposes_exactly_the_expected_commands_and_flags():
    """Names alone are not enough: a destructive capability can arrive as a flag on
    a command that is already allowed, leaving the command list untouched."""
    assert _command_surface() == EXPECTED_COMMANDS, (
        "the agent-reachable surface changed; a new command or flag needs a "
        "deliberate decision, not a passing test"
    )


def _telethon_calls(*relative: str) -> tuple[set[str], set[str]]:
    """Every method invoked on a Telethon client, and every raw request sent.

    `getattr(client, "delete_" + "dialog")` is deliberately reported as the
    unresolvable call it is: a scan that silently ignored it would let any name
    through while this file claims to be the backstop that needs no denylist."""
    methods, requests = set(), set()
    for name in relative:
        for node in ast.walk(ast.parse(_read_for_scan(REPO / name))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "client"
            ):
                methods.add(func.attr)
            if isinstance(func, ast.Name) and func.id == "getattr" and node.args:
                target = node.args[0]
                if isinstance(target, ast.Name) and target.id == "client":
                    methods.add(
                        target.id
                        + ".<dynamic>"
                        + (
                            f" ({ast.unparse(node.args[1])})"
                            if len(node.args) > 1
                            else ""
                        )
                    )
            if isinstance(func, ast.Name) and func.id == "client":
                for argument in node.args:
                    if isinstance(argument, ast.Call):
                        inner = argument.func
                        requests.add(
                            inner.id if isinstance(inner, ast.Name) else inner.attr
                        )
    return methods, requests


def _gateway_sources() -> list[str]:
    """Every module under src/ except the out-of-process login program, so a new
    file with direct Telethon calls cannot sidestep the scan."""
    return [
        str(path.relative_to(REPO))
        for path in sorted((REPO / "src").rglob("*.py"))
        if path.name != "login.py"
    ]


def test_the_gateway_telethon_surface_is_frozen():
    """The one guard that does not depend on knowing a forbidden name in advance:
    whatever the agent-reachable code calls on Telegram must be on this list."""
    methods, requests = _telethon_calls(*_gateway_sources())
    assert methods <= ALLOWED_GATEWAY_CALLS, (
        f"unapproved Telethon calls: {sorted(methods - ALLOWED_GATEWAY_CALLS)}"
    )
    assert requests <= ALLOWED_RAW_REQUESTS, (
        f"unapproved raw requests: {sorted(requests - ALLOWED_RAW_REQUESTS)}"
    )


def test_the_gateway_cannot_authenticate():
    """"No agent-visible login" is a stated non-goal; this is what enforces it."""
    methods, _ = _telethon_calls(*_gateway_sources())
    assert methods & ALLOWED_LOGIN_ONLY_CALLS == set(), (
        f"the gateway reaches a login call: {sorted(methods & ALLOWED_LOGIN_ONLY_CALLS)}"
    )


def test_the_login_program_stays_within_its_own_surface():
    methods, _ = _telethon_calls("src/telegram_plugin/login.py")
    allowed = ALLOWED_GATEWAY_CALLS | ALLOWED_LOGIN_ONLY_CALLS
    assert methods <= allowed, f"unapproved login calls: {sorted(methods - allowed)}"


def test_the_telethon_surface_scan_actually_finds_calls():
    """Positive control: a scan that silently found nothing would pass forever."""
    methods, requests = _telethon_calls(*_gateway_sources())
    assert "iter_messages" in methods
    assert "send_message" in methods
    assert requests == {"CheckChatInviteRequest"}
    login_methods, _ = _telethon_calls("src/telegram_plugin/login.py")
    assert "qr_login" in login_methods


def test_every_excluded_file_still_guards_the_vocabulary_it_is_exempt_from():
    """A nominal check — 'contains the word assert' — would let an exempt file stop
    guarding and become the one unwatched place to hide what the sweep looks for."""
    for name in ABSENCE_GUARDS:
        path = REPO / name
        assert path.exists(), name
        text = path.read_text()
        assert re.search(r"assert (not|never)|== \{\}|== set\(\)|not in ", text), name

    here = (REPO / "tests/test_security_tooling.py").read_text()
    for constant in ("MCP_RUNTIME", "FORBIDDEN_SIDE_EFFECTS", "EXPECTED_COMMANDS"):
        assert here.count(constant) >= 2, f"{constant} is declared but never enforced"
