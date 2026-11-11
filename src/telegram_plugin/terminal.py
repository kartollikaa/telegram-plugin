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

from telegram_plugin.qr import DARK_ON_LIGHT, RESET, render, render_for_chat

ALTERNATE_SCREEN = "\x1b[?1049h"
MAIN_SCREEN = "\x1b[?1049l"
HIDE_CURSOR = "\x1b[?25l"
SHOW_CURSOR = "\x1b[?25h"
CLEAR_SCREEN = "\x1b[H\x1b[2J"
CLEAR_TO_END = "\x1b[J"

HEADER = "Log in to Telegram: on your phone open Settings → Devices → Link Desktop Device"
FOOTER = "New code in {seconds:>2}s  ·  p  phone number instead  ·  Ctrl-C  cancel"


def is_interactive(stdin: TextIO, stdout: TextIO) -> bool:
    return stdin.isatty() and stdout.isatty()


def _rows(line: str, columns: int) -> int:
    return max(1, -(-len(line) // max(columns, 1)))


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
        plain = render(url, color=False)
        width = len(plain[0])
        columns, rows = shutil.get_terminal_size()
        frame = [HEADER, ""]
        needed_rows = len(plain) + 4
        if columns < width or rows < needed_rows:
            frame.append(
                f"The window is {columns}x{rows}; the code needs {width}x{needed_rows}. "
                "Enlarge it, or press p."
            )
        # Text lines wrap in a narrow window; the footer row has to count that.
        self._footer_row = sum(_rows(line, columns) for line in frame) + len(plain) + 2
        frame += [f"{DARK_ON_LIGHT}{line}{RESET}" for line in plain] + [""]
        self._write(CLEAR_SCREEN + "\n".join(frame) + "\n" + FOOTER.format(seconds=seconds))

    def countdown(self, seconds: int) -> None:
        self.notice(FOOTER.format(seconds=seconds))

    def notice(self, text: str) -> None:
        if self._footer_row is None:
            return
        self._write(f"\x1b[{self._footer_row};1H{CLEAR_TO_END}{text}")

    def poll_key(self) -> str | None:
        fd = self._in.fileno()
        ready, _, _ = select.select([fd], [], [], 0)
        if not ready:
            return None
        # Everything waiting at once: a key on a non-Latin layout is several bytes.
        return os.read(fd, 64).decode("utf-8", "ignore") or None

    def leave_qr(self) -> None:
        if self._saved_mode is None:
            return
        self._write(SHOW_CURSOR + MAIN_SCREEN)
        saved, self._saved_mode, self._footer_row = self._saved_mode, None, None
        try:
            termios.tcsetattr(self._in.fileno(), termios.TCSADRAIN, saved)
        except (termios.error, OSError):
            pass  # a closed terminal has no mode left to restore

    def _write(self, text: str) -> None:
        try:
            self._out.write(text)
            self._out.flush()
        except OSError:
            pass  # the terminal went away; the outcome still reaches the status file


class QrOutput:
    """QR frames for an agent's streaming command output, without a terminal."""

    def __init__(self, *, theme: str = "dark") -> None:
        self._theme = theme

    def say(self, text: str = "") -> None:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()

    def ask(self, prompt: str) -> str:
        raise RuntimeError("hidden terminal input is required")

    def ask_secret(self, prompt: str) -> str:
        raise RuntimeError("hidden terminal input is required")

    def enter_qr(self) -> None:
        pass

    def show_qr(self, url: str, seconds: int) -> None:
        frame = [HEADER, f"QR code — new code in {seconds}s:",
                 *render_for_chat(url, theme=self._theme), "Waiting for approval on your phone…"]
        self.say("\n".join(frame))

    def countdown(self, seconds: int) -> None:
        pass

    def notice(self, text: str) -> None:
        self.say(text)

    def poll_key(self) -> str | None:
        return None

    def leave_qr(self) -> None:
        pass
