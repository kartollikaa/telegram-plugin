"""The real terminal layer on a real pseudo-terminal: keys, hidden prompts, Ctrl-C,
hangup, and a terminal handed back the way it was found. Telethon is the double."""

import fcntl
import json
import os
import select
import struct
import subprocess
import sys
import termios
import time
from pathlib import Path

import pytest

from telegram_plugin.terminal import ALTERNATE_SCREEN, CLEAR_SCREEN, MAIN_SCREEN

REPO = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX terminals only")


class Operator:
    def __init__(self, tmp_path, scenario):
        self.state = tmp_path
        try:
            self.master, slave = os.openpty()
        except PermissionError:
            pytest.skip("this environment refuses to open a pseudo-terminal")
        fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
        environment = {**os.environ, "PYTHONPATH": f"{REPO / 'src'}:{REPO}"}
        for leaked in ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_STATE_DIR"):
            environment.pop(leaked, None)
        environment.update(TELEGRAM_API_ID="81234567", TELEGRAM_API_HASH="ab" * 16,
                           TERM="xterm-256color")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "tests.login_pty_child", str(tmp_path), scenario],
            stdin=slave, stdout=slave, stderr=slave, cwd=REPO, env=environment,
            start_new_session=True,
        )
        os.close(slave)
        self.seen = ""

    def expect(self, text, timeout=10.0, count=1):
        deadline = time.monotonic() + timeout
        while self.seen.count(text) < count:
            left = deadline - time.monotonic()
            assert left > 0, f"never saw {text!r}; screen so far:\n{self.seen[-1500:]}"
            if select.select([self.master], [], [], left)[0]:
                try:
                    chunk = os.read(self.master, 65536)
                except OSError:
                    chunk = b""
                assert chunk, f"exited before {text!r}:\n{self.seen[-1500:]}"
                self.seen += chunk.decode("utf-8", "replace")
        return self.seen

    def type(self, text):
        os.write(self.master, text.encode())

    def finish(self, timeout=10.0):
        try:
            self.expect("TTY-", timeout)
        except AssertionError:
            pass
        code = self.process.wait(timeout)
        os.close(self.master)
        return code

    def status(self):
        return json.loads((self.state / "auth-status.json").read_text())


def test_qr_then_phone_then_password_on_a_real_terminal(tmp_path):
    operator = Operator(tmp_path, "phone_then_password")
    operator.expect("Choose login method [1/2]: ")
    operator.type("2\r")
    operator.expect(ALTERNATE_SCREEN)
    operator.expect("Link Desktop Device")
    operator.expect("▀")
    operator.expect("New code in")
    # The double's token lives 0.8 s; a second drawing means it rotated in place.
    operator.expect(CLEAR_SCREEN, timeout=5, count=2)
    assert operator.seen.count(ALTERNATE_SCREEN) == 1, "a rotation left the code screen"

    operator.type("p")
    operator.expect(MAIN_SCREEN)
    operator.expect("Phone number, with country code: ")
    operator.type("+44 7700 900123\r")
    operator.expect("Login code (hidden as you type): ")
    mark = len(operator.seen)
    operator.type("24680\r")
    operator.expect("Password hint: usual one")
    operator.expect("Password (hidden as you type): ")
    operator.type("first-guess\r")
    operator.expect("Wrong password. 2 tries left.")
    # getpass discards anything typed before its prompt appears, as a real one would.
    operator.expect("Password (hidden as you type): ", count=2)
    operator.type("second-guess\r")
    operator.expect("Logged in as Test (id 42)")
    assert operator.finish() == 0

    after_prompts = operator.seen[mark:]
    assert "24680" not in after_prompts, "the login code was echoed"
    assert "first-guess" not in after_prompts and "second-guess" not in after_prompts
    assert "+44 7700 900123" in operator.seen, "the phone number is typed visibly"
    assert "TTY-RESTORED" in operator.seen
    assert operator.status()["state"] == "authorized"


def test_ctrl_c_on_the_code_screen_cancels_and_hands_the_terminal_back(tmp_path):
    operator = Operator(tmp_path, "never_scanned")
    operator.expect("Choose login method [1/2]: ")
    operator.type("2\r")
    operator.expect("New code in")
    operator.type("\x03")
    operator.expect("Login cancelled; check --status before retrying.")
    assert operator.finish() == 130
    assert operator.seen.rindex(MAIN_SCREEN) > operator.seen.rindex(ALTERNATE_SCREEN)
    assert "TTY-RESTORED" in operator.seen
    assert operator.status()["state"] == "cancelled"


def test_ctrl_c_at_a_prompt_cancels_at_once(tmp_path):
    """asyncio's own Ctrl-C handler only schedules a cancel, which a blocking prompt
    never notices; the login replaces it so one press is enough."""
    operator = Operator(tmp_path, "phone_then_password")
    operator.expect("Choose login method [1/2]: ")
    operator.type("2\r")
    operator.expect("New code in")
    operator.type("p")
    operator.expect("Phone number, with country code: ")
    operator.type("\x03")
    operator.expect("Login cancelled", timeout=5)
    assert operator.finish() == 130
    assert operator.status()["state"] == "cancelled"


def test_closing_the_terminal_still_leaves_an_outcome(tmp_path):
    operator = Operator(tmp_path, "never_scanned")
    operator.expect("Choose login method [1/2]: ")
    operator.type("2\r")
    operator.expect("New code in")
    os.close(operator.master)
    operator.process.wait(10)
    payload = operator.status()
    assert payload["state"] == "cancelled"
    assert "terminal was closed" in payload["hint"]


def test_phone_choice_needs_no_qr_on_a_real_terminal(tmp_path):
    operator = Operator(tmp_path, "phone_then_password")
    operator.expect("Choose login method [1/2]: ")
    operator.type("1\r")
    operator.expect("Phone number, with country code: ")
    operator.type("+447700900123\r")
    operator.expect("Login code (hidden as you type): ")
    operator.type("12345\r")
    operator.expect("Password (hidden as you type): ")
    operator.type("wrong\r")
    operator.expect("Password (hidden as you type): ", count=2)
    operator.type("right\r")
    operator.expect("Logged in as Test")
    assert operator.finish() == 0
    assert ALTERNATE_SCREEN not in operator.seen
    assert "12345" not in operator.seen
    assert "TTY-RESTORED" in operator.seen


def test_ctrl_c_at_method_menu_leaves_a_cancelled_outcome(tmp_path):
    operator = Operator(tmp_path, "never_scanned")
    operator.expect("Choose login method [1/2]: ")
    operator.type("\x03")
    operator.expect("Login cancelled")
    assert operator.finish() == 130
    assert operator.status()["state"] == "cancelled"


def test_logout_names_the_account_before_confirmation(tmp_path):
    operator = Operator(tmp_path, "logout")
    operator.expect("Log out Test (id 42) from this plugin? [y/N]: ")
    operator.type("yes\r")
    operator.expect("Logged out of Telegram")
    assert operator.finish() == 0
    assert operator.status()["state"] == "logged_out"


def test_declining_logout_in_a_real_terminal_preserves_session(tmp_path):
    operator = Operator(tmp_path, "logout")
    operator.expect("Log out Test (id 42) from this plugin? [y/N]: ")
    operator.type("\r")
    operator.expect("Logout cancelled")
    assert operator.finish() == 1
    assert operator.status()["state"] == "cancelled"
    assert (tmp_path / "telegram.session").exists()
