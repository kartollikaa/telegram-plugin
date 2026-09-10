"""Configuration: real environment beats the state-directory .env beats defaults."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

DEPENDENCIES: tuple[str, ...] = ("telethon>=1.42,<2", "mcp>=2,<3")

# Below this, Telethon honoured an absolute path in a sender-supplied file name.
MIN_TELETHON = (1, 42)

DEFAULT_STATE_SUBPATH = ".local/state/telegram-plugin"
DEFAULT_SESSION_NAME = "telegram"
DEFAULT_MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024
DEFAULT_SEND_LIMIT = 20
DEFAULT_IDLE_TIMEOUT = 60.0
DEFAULT_LOCK_WAIT = 20.0


def parse_dotenv(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw = line.partition("=")
        value = raw.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


@dataclass(frozen=True)
class Config:
    api_id: int | None
    api_hash: str | None
    state_dir: Path
    session_name: str
    allow_send: bool
    output_root: Path
    max_download_bytes: int
    send_limit: int
    idle_timeout: float
    lock_wait: float
    unreadable: tuple[str, ...] = field(default=())
    """Variables that were set but could not be read as a number, so a default stands in."""

    @property
    def session_path(self) -> Path:
        return self.state_dir / f"{self.session_name}.session"

    @property
    def dotenv_path(self) -> Path:
        return self.state_dir / ".env"

    @property
    def auth_status_path(self) -> Path:
        """Where a login in progress publishes its link and its outcome."""
        return self.state_dir / "auth-status.json"


def load_config(env: Mapping[str, str], dotenv_text: str | None = None) -> Config:
    from_file = parse_dotenv(dotenv_text) if dotenv_text else {}

    def value(key: str) -> str | None:
        """A host that exports an empty value has spoken; it must not fall through to the file."""
        return env[key] if key in env else from_file.get(key)

    home = Path(env.get("HOME") or Path.home())
    state_raw = value("TELEGRAM_STATE_DIR")
    state_dir = Path(state_raw) if state_raw else home / DEFAULT_STATE_SUBPATH
    output_raw = value("TELEGRAM_OUTPUT_ROOT")
    unreadable: list[str] = []

    return Config(
        api_id=_as_int(value("TELEGRAM_API_ID")),
        api_hash=value("TELEGRAM_API_HASH"),
        state_dir=state_dir,
        session_name=value("TELEGRAM_SESSION_NAME") or DEFAULT_SESSION_NAME,
        allow_send=value("TELEGRAM_PLUGIN_ALLOW_SEND") == "1",
        output_root=Path(output_raw) if output_raw else state_dir / "downloads",
        max_download_bytes=_ceiling(
            "TELEGRAM_MAX_DOWNLOAD_BYTES", value, DEFAULT_MAX_DOWNLOAD_BYTES, unreadable
        ),
        send_limit=_ceiling(
            "TELEGRAM_PLUGIN_SEND_LIMIT", value, DEFAULT_SEND_LIMIT, unreadable
        ),
        idle_timeout=_seconds("TELEGRAM_IDLE_TIMEOUT", value, DEFAULT_IDLE_TIMEOUT, unreadable),
        lock_wait=_seconds("TELEGRAM_LOCK_WAIT", value, DEFAULT_LOCK_WAIT, unreadable),
        unreadable=tuple(unreadable),
    )


def _seconds(key: str, read, fallback: float, unreadable: list[str]) -> float:
    """Zero is a deliberate opt-out; a negative value is a typo, not an instruction.

    Letting it through would silently restore the behaviour where one session holds
    the account for its whole life.
    """
    raw = read(key)
    try:
        seconds = float(raw) if raw else fallback
    except ValueError:
        unreadable.append(key)
        return fallback
    return seconds if seconds >= 0 else fallback


def _ceiling(key: str, read, default: int, unreadable: list[str]) -> int:
    """0 is a valid ceiling — the strictest one — so `or default` would silently loosen it.

    A negative clamps to 0 rather than to the default: for a ceiling the safe direction
    is refusing everything, where for a timeout above it the safe direction is the
    fallback. An unreadable value defaults and is named in the startup summary.
    """
    raw = read(key)
    parsed = _as_int(raw)
    if parsed is not None:
        return max(parsed, 0)
    if raw:
        unreadable.append(key)
    return default


def _as_int(raw: str | None) -> int | None:
    """A malformed number must not crash startup with the value in the traceback."""
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


def load_config_from_environment(env: Mapping[str, str]) -> Config:
    """Two passes: the first locates the state directory, the second reads its .env."""
    bootstrap = load_config(env)
    try:
        text = bootstrap.dotenv_path.read_text(encoding="utf-8")
    except OSError:
        return bootstrap
    return load_config(env, text)
