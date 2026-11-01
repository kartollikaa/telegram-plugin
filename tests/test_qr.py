"""The terminal QR: what is drawn must be exactly the code, and must fit a terminal."""

import base64
import builtins

import pytest

from telegram_plugin import qr
from telegram_plugin.qr import DARK_ON_LIGHT, QrUnavailable, qr_matrix, render

# A login URL is `tg://login?token=` plus a 32-byte token in unpadded base64url.
LOGIN_URL = "tg://login?token=" + base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("=")

_HALVES = {"█": (True, True), "▀": (True, False), "▄": (False, True), " ": (False, False)}


def _decode(lines):
    """Read the half-block text back into module rows, the way a camera would see it."""
    rows = []
    for line in lines:
        pairs = [_HALVES[cell] for cell in line]
        rows.append([top for top, _ in pairs])
        rows.append([bottom for _, bottom in pairs])
    return rows


def test_the_drawing_is_exactly_the_code():
    matrix = qr_matrix(LOGIN_URL)
    drawn = _decode(render(LOGIN_URL, color=False))
    assert drawn[: len(matrix)] == matrix
    # An odd module count is padded with one light row, which is quiet zone.
    assert all(not module for row in drawn[len(matrix):] for module in row)


def test_the_decoder_catches_swapped_halves():
    """Positive control: a renderer that swapped top and bottom must not pass."""
    swapped = [line.translate(str.maketrans("▀▄", "▄▀")) for line in render(LOGIN_URL, color=False)]
    assert _decode(swapped)[: len(qr_matrix(LOGIN_URL))] != qr_matrix(LOGIN_URL)


def test_a_login_code_fits_an_80_by_24_terminal_with_room_for_its_text():
    lines = render(LOGIN_URL, color=False)
    assert len(lines[0]) <= 40
    assert len(lines) <= 19, "header, blank, code, blank and footer must fit 24 rows"


def test_the_code_keeps_a_quiet_zone_on_every_side():
    matrix = qr_matrix(LOGIN_URL)
    for edge in (matrix[0], matrix[1], matrix[-1], matrix[-2]):
        assert not any(edge)
    assert not any(row[0] or row[1] or row[-1] or row[-2] for row in matrix)


def test_colours_are_explicit_so_a_dark_theme_cannot_invert_the_code():
    for line in render(LOGIN_URL):
        assert line.startswith(DARK_ON_LIGHT)
        assert line.endswith(qr.RESET)


def test_a_runtime_without_the_library_says_how_to_fix_it(monkeypatch):
    real_import = builtins.__import__

    def without_segno(name, *args, **kwargs):
        if name == "segno":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_segno)
    with pytest.raises(QrUnavailable, match="scripts/setup.sh"):
        render(LOGIN_URL)
