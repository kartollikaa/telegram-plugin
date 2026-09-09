"""Configuration: real environment beats the state-directory .env beats defaults."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEPENDENCIES: tuple[str, ...] = ("telethon>=1.36", "mcp>=2,<3")

DEFAULT_STATE_SUBPATH = ".local/state/telegram-plugin"
DEFAULT_SESSION_NAME = "telegram"


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

    @property
    def session_path(self) -> Path:
        return self.state_dir / f"{self.session_name}.session"

    @property
    def dotenv_path(self) -> Path:
        return self.state_dir / ".env"


def load_config(env: Mapping[str, str], dotenv_text: str | None = None) -> Config:
    from_file = parse_dotenv(dotenv_text) if dotenv_text else {}

    def value(key: str) -> str | None:
        return env.get(key) or from_file.get(key)

    home = Path(env.get("HOME") or Path.home())
    state_raw = value("TELEGRAM_STATE_DIR")
    state_dir = Path(state_raw) if state_raw else home / DEFAULT_STATE_SUBPATH
    api_id = value("TELEGRAM_API_ID")
    output_raw = value("TELEGRAM_OUTPUT_ROOT")

    return Config(
        api_id=int(api_id) if api_id else None,
        api_hash=value("TELEGRAM_API_HASH"),
        state_dir=state_dir,
        session_name=value("TELEGRAM_SESSION_NAME") or DEFAULT_SESSION_NAME,
        allow_send=value("TELEGRAM_PLUGIN_ALLOW_SEND") == "1",
        output_root=Path(output_raw) if output_raw else state_dir / "downloads",
    )


def load_config_from_environment(env: Mapping[str, str]) -> Config:
    """Two passes: the first locates the state directory, the second reads its .env."""
    bootstrap = load_config(env)
    try:
        text = bootstrap.dotenv_path.read_text(encoding="utf-8")
    except OSError:
        return bootstrap
    return load_config(env, text)
