"""Wiring only. The script itself needs the network (pip-audit), so the suite
asserts that it exists, covers the four tools and is what CI runs; the proof
that each check can fail lives in the positive-control runs recorded in the
pull request."""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts/security-check.sh"
WORKFLOW = REPO / ".github/workflows/ci.yml"


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
    for entry in ("bin/telegram-mcp", "bin/telegram-login", "scripts/*.sh"):
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
