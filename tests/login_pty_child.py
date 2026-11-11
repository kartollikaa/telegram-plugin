"""The login as the operator meets it, with Telethon replaced: run by test_login_pty
on the slave side of a pseudo-terminal. Argument: state directory, scenario name."""

import fcntl
import os
import sys
import termios
from pathlib import Path

from telegram_plugin.login import Pace, main
from tests.login_doubles import StubClient, StubQr

SCENARIOS = {
    "phone_then_password": lambda: StubClient(
        StubQr(lifetime=0.8), codes=["password"], passwords=["wrong", "ok"], hint="usual one"
    ),
    "never_scanned": lambda: StubClient(StubQr(lifetime=0.8)),
    "streamed_qr": lambda: StubClient(StubQr(lifetime=0.3, scan_after=1.0)),
    "logout": lambda: StubClient(authorized=True),
}


def main_child() -> int:
    state_dir, scenario = sys.argv[1], sys.argv[2]
    try:
        # Become the terminal's session owner, so Ctrl-C and hangup reach us as they would.
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    except OSError:
        pass
    client = SCENARIOS[scenario]()
    pace = Pace(rotate_after=0.05, min_show=0.2, max_show=5.0, limit=60.0, tick=0.02)
    arguments = ["--state-dir", state_dir, *sys.argv[3:]]
    if scenario == "logout":
        Path(state_dir, "telegram.session").touch()
        arguments.append("--logout")
    code = main(arguments, client_factory=lambda _: client, pace=pace)
    if not os.isatty(0):
        return code
    attributes = termios.tcgetattr(0)
    restored = attributes[3] & termios.ECHO and attributes[3] & termios.ICANON
    print("TTY-RESTORED" if restored else "TTY-LEFT-RAW", flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main_child())
