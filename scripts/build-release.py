"""Build a local plugin distribution with the publisher's application profile."""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from telegram_plugin.application_profile import parse_application_profile
from telegram_plugin.config import parse_dotenv
from telegram_plugin.state import read_private_text


def metadata(entry: tarfile.TarInfo) -> tarfile.TarInfo:
    entry.uid = entry.gid = entry.mtime = 0
    entry.uname = entry.gname = ""
    return entry


def build(credentials_file: Path, output: Path) -> None:
    values = parse_dotenv(read_private_text(credentials_file))
    try:
        text = json.dumps({"api_id": int(values["TELEGRAM_API_ID"]),
                           "api_hash": values["TELEGRAM_API_HASH"]})
        parse_application_profile(text)
    except (KeyError, ValueError):
        raise ValueError("The publisher file needs a valid TELEGRAM_API_ID and TELEGRAM_API_HASH") from None
    names = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream, tarfile.open(fileobj=stream, mode="w:gz") as archive:
            for name in names:
                source = ROOT / name
                if (source.resolve() == credentials_file.resolve()
                        or (name != ".env.example" and any(
                            part == ".env" or part.startswith(".env.")
                            or ".session" in part for part in Path(name).parts))
                        or source.name in {"_application.json", "auth-status.json"}):
                    continue
                if (name in {"README.md", "LICENSE", "pyproject.toml", "plugin.json", ".env.example",
                             ".claude-plugin/plugin.json", "scripts/setup.sh"}
                        or (name.startswith("src/telegram_plugin/") and name.endswith(".py"))
                        or (name.startswith(("skills/", "docs/")) and name.endswith(".md"))
                        or name in {"bin/telegram", "bin/telegram-login"}):
                    if (source.is_symlink() or not source.is_file()
                            or not source.resolve().is_relative_to(ROOT)):
                        raise ValueError("Release inputs must be regular tracked files")
                    archive.add(source, arcname=f"telegram-plugin/{name}", recursive=False,
                                filter=metadata)
            profile = text.encode()
            entry = tarfile.TarInfo("telegram-plugin/src/telegram_plugin/_application.json")
            entry.size, entry.mode = len(profile), 0o600
            archive.addfile(entry, io.BytesIO(profile))
    except BaseException:
        output.unlink()
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        build(args.credentials_file, args.output)
    except (OSError, ValueError, subprocess.SubprocessError):
        print("Release build failed: check the publisher file and unused output path.", file=sys.stderr)
        return 1
    print(f"Distribution prepared: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
