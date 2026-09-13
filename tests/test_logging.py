from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


# Transport entry points own their streams; every shared module remains stream-free.
STREAM_EXEMPT = {"login.py", "cli.py"}


def _shared_modules():
    return [
        path
        for path in (REPO / "src/telegram_plugin").rglob("*.py")
        if path.name not in STREAM_EXEMPT
    ]


# Write-shaped forms only. Matching the bare word "stderr" also flagged docstrings
# that explain why the rule exists, which is how a guard starts forbidding its own
# rationale. A module that actually writes uses one of these.
STREAM_WRITES = ("sys.stderr", "sys.stdout", "print(", "os.write(1", "os.write(2")


def stream_writes_in(source: str) -> list[str]:
    return [marker for marker in STREAM_WRITES if marker in source]


def test_no_shared_module_touches_a_stream_directly():
    offenders = {
        path.name: found
        for path in _shared_modules()
        if (found := stream_writes_in(path.read_text()))
    }
    assert offenders == {}


def test_the_guard_catches_a_write_and_ignores_prose_about_one():
    """A guard that cannot fail proves nothing — and one that fires on its own
    rationale forbids explaining itself."""
    assert stream_writes_in("def leak(m):\n    print(m)\n") == ["print("]
    assert stream_writes_in('    handle.write(m)\n') == []
    assert stream_writes_in("import sys\nsys.stderr.write(m)\n") == ["sys.stderr"]
    assert stream_writes_in('"""Diagnostics go to stderr, never stdout."""\n') == []


def test_the_exemptions_are_the_ones_we_think_they_are():
    names = {path.name for path in (REPO / "src/telegram_plugin").rglob("*.py")}
    assert STREAM_EXEMPT <= names


def test_the_login_cli_keeps_its_secret_prompt_off_the_screen():
    assert "getpass.getpass" in (REPO / "src/telegram_plugin/login.py").read_text()
