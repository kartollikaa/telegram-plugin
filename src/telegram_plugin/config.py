"""Configuration: real environment beats the state-directory .env beats defaults."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEPENDENCIES: tuple[str, ...] = ("telethon>=1.42,<2", "mcp>=2,<3")

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
        return env.get(key) or from_file.get(key)

    home = Path(env.get("HOME") or Path.home())
    state_raw = value("TELEGRAM_STATE_DIR")
    state_dir = Path(state_raw) if state_raw else home / DEFAULT_STATE_SUBPATH
    output_raw = value("TELEGRAM_OUTPUT_ROOT")

    return Config(
        api_id=_as_int(value("TELEGRAM_API_ID")),
        api_hash=value("TELEGRAM_API_HASH"),
        state_dir=state_dir,
        session_name=value("TELEGRAM_SESSION_NAME") or DEFAULT_SESSION_NAME,
        allow_send=value("TELEGRAM_PLUGIN_ALLOW_SEND") == "1",
        output_root=Path(output_raw) if output_raw else state_dir / "downloads",
        max_download_bytes=_as_int(value("TELEGRAM_MAX_DOWNLOAD_BYTES"))
        or DEFAULT_MAX_DOWNLOAD_BYTES,
        send_limit=_as_int(value("TELEGRAM_PLUGIN_SEND_LIMIT")) or DEFAULT_SEND_LIMIT,
        idle_timeout=_as_float(value("TELEGRAM_IDLE_TIMEOUT"), DEFAULT_IDLE_TIMEOUT),
        lock_wait=_as_float(value("TELEGRAM_LOCK_WAIT"), DEFAULT_LOCK_WAIT),
    )


def _as_float(raw: str | None, fallback: float) -> float:
    try:
        return float(raw) if raw else fallback
    except ValueError:
        return fallback


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
