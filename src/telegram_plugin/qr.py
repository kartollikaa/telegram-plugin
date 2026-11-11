"""A QR code as terminal text: one character cell carries two modules, top and bottom."""

from __future__ import annotations

# Two modules is half the spec's margin, and enough: the code sits on its own white
# box, and four would push a login screen past a 24-row terminal.
QUIET_ZONE = 2

# Black on white whatever the terminal theme: phone scanners expect dark modules on
# a light ground, and a dark theme would otherwise invert the code.
DARK_ON_LIGHT = "\x1b[38;5;16;48;5;231m"
RESET = "\x1b[0m"

_CELL = {(True, True): "█", (True, False): "▀", (False, True): "▄", (False, False): " "}


class QrUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            "The QR library (segno) is missing from this runtime. Run scripts/setup.sh, "
            "or install it into TELEGRAM_PLUGIN_PYTHON: python -m pip install 'segno>=1.6,<2'."
        )


def qr_matrix(text: str, *, quiet_zone: int = QUIET_ZONE) -> list[list[bool]]:
    """Rows of modules, dark is True, quiet zone included."""
    try:
        import segno
    except ImportError as exc:
        raise QrUnavailable() from exc
    # Lowest correction keeps a login link at version 4, 33 modules square; segno
    # boosts the level anyway when the same version has room for it.
    code = segno.make(text, error="l", micro=False)
    return [[bool(module) for module in row] for row in code.matrix_iter(border=quiet_zone)]


def render(text: str, *, quiet_zone: int = QUIET_ZONE, color: bool = True) -> list[str]:
    matrix = qr_matrix(text, quiet_zone=quiet_zone)
    if len(matrix) % 2:
        matrix.append([False] * len(matrix[0]))
    lines = []
    for top, bottom in zip(matrix[0::2], matrix[1::2]):
        cells = "".join(_CELL[pair] for pair in zip(top, bottom))
        lines.append(f"{DARK_ON_LIGHT}{cells}{RESET}" if color else cells)
    return lines


def render_for_chat(text: str) -> list[str]:
    """Opposite polarities keep a light quiet zone on either chat theme."""
    lines = render(text, color=False)
    inverse = str.maketrans("█▀▄ ", " ▄▀█")
    return [*lines, "", *(line.translate(inverse) for line in lines)]
