"""Which commands this install actually provides.

A checkout — including the publisher tarball, which keeps the same layout —
is driven through the bin/ launchers; a package-manager install exposes the
console scripts instead. Hints and usage text must name what exists.
"""

from __future__ import annotations

import os
from pathlib import Path

CHECKOUT_ROOT = Path(__file__).resolve().parents[2]


def in_checkout() -> bool:
    return (CHECKOUT_ROOT / "pyproject.toml").is_file()


def cli_prog() -> str:
    return "telegram" if in_checkout() else "telegram-personal"


def login_prog() -> str:
    return "telegram-login" if in_checkout() else "telegram-personal-login"


def login_command() -> str:
    """How a human runs the login: a path inside a checkout, a command installed."""
    return f"bin/{login_prog()}" if in_checkout() else login_prog()


def tighten_umask() -> None:
    """bin/* set umask 077 in the shell before exec; a console script has no
    shell, so the process tightens its own before anything can create a file."""
    os.umask(0o077)
