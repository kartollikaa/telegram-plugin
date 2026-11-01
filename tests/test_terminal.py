"""The terminal layer's arithmetic, without a terminal: where the footer is, and what
happens when the terminal is already gone."""

import io
import os
import re
import termios

import pytest

from telegram_plugin import terminal
from telegram_plugin.terminal import HEADER, Terminal

URL = "tg://login?token=" + "A" * 43


def _footer_row_after_drawing(monkeypatch, columns, rows):
    monkeypatch.setattr(terminal.shutil, "get_terminal_size",
                        lambda: os.terminal_size((columns, rows)))
    out = io.StringIO()
    screen = Terminal(stdin=io.StringIO(), stdout=out)
    screen.show_qr(URL, 25)
    screen.countdown(24)
    return int(re.findall(r"\x1b\[(\d+);1H", out.getvalue())[-1]), out.getvalue()


def test_the_countdown_lands_on_the_footer_in_a_wide_window(monkeypatch):
    row, drawn = _footer_row_after_drawing(monkeypatch, 120, 40)
    lines_before_footer = drawn.split("\x1b[H\x1b[2J")[1].split("New code in")[0].count("\n")
    assert row == lines_before_footer + 1


def test_the_countdown_counts_wrapped_lines_in_a_narrow_window(monkeypatch):
    """The header wraps at 40 columns; a footer row counted in lines, not rows, made
    every countdown overwrite the row under the code."""
    row, drawn = _footer_row_after_drawing(monkeypatch, 40, 40)
    lines_before_footer = drawn.split("\x1b[H\x1b[2J")[1].split("New code in")[0].count("\n")
    header_rows = -(-len(HEADER) // 40)
    assert header_rows > 1
    assert row == lines_before_footer + header_rows


def test_handing_back_a_closed_terminal_does_not_raise(monkeypatch):
    def gone(*args):
        raise termios.error(5, "Input/output error")

    monkeypatch.setattr(terminal.termios, "tcsetattr", gone)
    screen = Terminal(stdin=_FakeTty(), stdout=io.StringIO())
    screen._saved_mode = [0, 0, 0, 0, 0, 0, []]
    screen.leave_qr()


class _FakeTty(io.StringIO):
    def fileno(self):
        return 0


@pytest.mark.parametrize("keys", ["p", "P", "з", "З"])
def test_the_phone_key_works_on_latin_and_russian_layouts(keys):
    from telegram_plugin.login import _wants_phone

    assert _wants_phone(keys)
    assert not _wants_phone("x")
    assert not _wants_phone(None)
