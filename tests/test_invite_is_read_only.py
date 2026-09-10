from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_the_joining_request_is_absent_from_the_codebase():
    sources = "\n".join(path.read_text() for path in (REPO / "src").rglob("*.py"))
    assert "ImportChatInvite" not in sources


def test_invite_resolution_uses_the_checking_request():
    assert "CheckChatInvite" in (REPO / "src/telegram_plugin/client.py").read_text()


def test_no_read_acknowledgement_is_ever_sent():
    sources = "\n".join(path.read_text() for path in (REPO / "src").rglob("*.py"))
    assert "send_read_acknowledge" not in sources
    assert "ReadHistory" not in sources
