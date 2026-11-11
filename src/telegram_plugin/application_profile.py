"""The publisher's client identity, supplied only in a built distribution."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

PROFILE_PATH = Path(__file__).with_name("_application.json")


@dataclass(frozen=True)
class ApplicationCredentials:
    api_id: int = field(repr=False)
    api_hash: str = field(repr=False)


def parse_application_profile(text: str) -> ApplicationCredentials:
    try:
        data = json.loads(text)
        api_id, api_hash = data["api_id"], data["api_hash"]
        if (set(data) != {"api_id", "api_hash"} or type(api_id) is not int
                or not 0 < api_id < 2**31 or not isinstance(api_hash, str)
                or not re.fullmatch(r"[0-9a-fA-F]{32}", api_hash)):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise ValueError("Invalid application profile; reinstall the publisher distribution") from None
    return ApplicationCredentials(api_id, api_hash)


def load_application_profile() -> ApplicationCredentials | None:
    try:
        text = PROFILE_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError):
        raise ValueError("Cannot read application profile; reinstall the publisher distribution") from None
    return parse_application_profile(text)
