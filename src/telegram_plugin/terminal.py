"""The login's one window to the operator: a real terminal, the QR redrawn in place."""

from __future__ import annotations

import getpass
import os
import select
import shutil
import sys
import termios
import tty
from typing import TextIO

from telegram_plugin.qr import render

ALTERNATE_SCREEN = "\x1b[?1049h"
MAIN_SCREEN = "\x1b[?1049l"
HIDE_CURSOR = "\x1b[?25l"
SHOW_CURSOR = "\x1b[?25h"
CLEAR_SCREEN = "\x1b[H\x1b[2J"
CLEAR_LINE = "\x1b[2K"

HEADER = "Log in to Telegram: on your phone open Settings → Devices → Link Desktop Device"
FOOTER = "New code in {seconds:>2}s  ·  p  use phone number instead  ·  Ctrl-C  cancel"


def is_interactive(stdin: TextIO, stdout: TextIO) -> bool:
    return stdin.isatty() and stdout.isatty()


class Terminal:
    """QR screen on the alternate buffer, so redrawing never scrolls and quitting
    leaves the operator's scrollback as it was; prompts on the main one."""

    def __init__(self, stdin: TextIO | None = None, stdout: TextIO | None = None) -> None:
        self._in = stdin or sys.stdin
        self._out = stdout or sys.stdout
        self._saved_mode: list | None = None
        self._footer_row: int | None = None

    def say(self, text: str = "") -> None:
        self._write(text + "\n")

    def ask(self, prompt: str) -> str:
        return input(prompt)

    def ask_secret(self, prompt: str) -> str:
        return getpass.getpass(prompt)

    def enter_qr(self) -> None:
        fd = self._in.fileno()
        self._saved_mode = termios.tcgetattr(fd)
        # cbreak, not raw: a single key arrives at once, and Ctrl-C still interrupts.
        tty.setcbreak(fd)
        self._write(ALTERNATE_SCREEN + HIDE_CURSOR)

    def show_qr(self, url: str, seconds: int) -> None:
        code = render(url)
        width = len(render(url, color=False)[0])
        frame = [HEADER, ""]
        columns, rows = shutil.get_terminal_size()
        needed_rows = len(code) + 4
        if columns < width or rows < needed_rows:
            frame.append(
                f"The window is {columns}x{rows}; the code needs {width}x{needed_rows}. "
                "Enlarge it, or press p."
            )
        frame += [*code, ""]
        self._footer_row = len(frame) + 1
        self._write(CLEAR_SCREEN + "\n".join(frame) + "\n" + FOOTER.format(seconds=seconds))

    def countdown(self, seconds: int) -> None:
        self.notice(FOOTER.format(seconds=seconds))

    def notice(self, text: str) -> None:
        if self._footer_row is None:
            return
        self._write(f"\x1b[{self._footer_row};1H{CLEAR_LINE}{text}")

    def poll_key(self) -> str | None:
        fd = self._in.fileno()
        ready, _, _ = select.select([fd], [], [], 0)
        if not ready:
            return None
        return os.read(fd, 1).decode("utf-8", "ignore") or None

    def leave_qr(self) -> None:
        if self._saved_mode is None:
            return
        self._write(SHOW_CURSOR + MAIN_SCREEN)
        termios.tcsetattr(self._in.fileno(), termios.TCSADRAIN, self._saved_mode)
        self._saved_mode = None
        self._footer_row = None

    def _write(self, text: str) -> None:
        try:
            self._out.write(text)
            self._out.flush()
        except OSError:
            pass  # the terminal went away; the outcome still reaches the status file
