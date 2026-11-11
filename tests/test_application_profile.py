import importlib.util
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from telegram_plugin import application_profile
from telegram_plugin.config import load_config

APP_ID = 81234567
APP_HASH = "ab" * 16
REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def profile(tmp_path, monkeypatch):
    path = tmp_path / "_application.json"
    path.write_text(json.dumps({"api_id": APP_ID, "api_hash": APP_HASH}))
    monkeypatch.setattr(application_profile, "PROFILE_PATH", path)
    return path


def test_bundled_profile_needs_no_user_api_configuration(profile):
    config = load_config({})
    assert config.api_id == APP_ID and config.api_hash == APP_HASH
    assert config.credential_source == "application"
    assert str(APP_ID) not in repr(config) and APP_HASH not in repr(config)


def test_personal_credentials_override_the_application_as_one_pair(profile):
    config = load_config({"TELEGRAM_API_ID": "99", "TELEGRAM_API_HASH": "personal"})
    assert config.api_id == 99 and config.api_hash == "personal"
    assert config.credential_source == "override"


@pytest.mark.parametrize("override", [{"TELEGRAM_API_ID": ""}, {"TELEGRAM_API_ID": "99"},
                                     {"TELEGRAM_API_HASH": "personal"}])
def test_partial_or_cleared_override_never_mixes_with_publisher_credentials(profile, override):
    config = load_config(override)
    assert config.api_id != APP_ID and config.api_hash != APP_HASH


def test_environment_override_never_borrows_half_the_pair_from_dotenv(profile):
    config = load_config({"TELEGRAM_API_ID": "99"},
                         "TELEGRAM_API_ID=77\nTELEGRAM_API_HASH=file-hash\n")
    assert config.api_id == 99 and config.api_hash is None
    complete = load_config({"TELEGRAM_API_ID": "99", "TELEGRAM_API_HASH": "env-hash"},
                           "TELEGRAM_API_ID=77\nTELEGRAM_API_HASH=file-hash\n")
    assert complete.api_id == 99 and complete.api_hash == "env-hash"


@pytest.mark.parametrize("text", ["[]", "null", "not-json", '{}', '{"api_id":true}',
                                  '{"api_id":1,"api_hash":"' + APP_HASH + '","extra":1}'])
def test_invalid_profile_fails_without_echoing_contents(profile, text):
    profile.write_text(text)
    with pytest.raises(ValueError) as caught:
        load_config({})
    assert APP_HASH not in str(caught.value)
    assert "reinstall" in str(caught.value)


def test_release_contains_profile_and_runs_status_without_user_configuration(tmp_path):
    source = tmp_path / ".env.publisher"
    source.write_text(f"TELEGRAM_API_ID={APP_ID}\nTELEGRAM_API_HASH={APP_HASH}\n")
    output = tmp_path / "plugin.tar.gz"
    build = subprocess.run([sys.executable, str(REPO / "scripts/build-release.py"),
                            "--credentials-file", str(source), "--output", str(output)],
                           capture_output=True, text=True, check=False, timeout=10)
    assert build.returncode == 0, build.stderr
    assert APP_HASH not in build.stdout + build.stderr
    with tarfile.open(output) as archive:
        names = archive.getnames()
        assert "telegram-plugin/src/telegram_plugin/_application.json" in names
        assert all(not name.endswith((".session", ".env", ".env.publisher")) for name in names)
        assert all(member.uname == "" and member.gname == "" for member in archive.getmembers())
        archive.extractall(tmp_path / "unpacked", filter="data")
    root = tmp_path / "unpacked/telegram-plugin"
    env = {key: value for key, value in os.environ.items() if not key.startswith("TELEGRAM_")}
    env.update({"HOME": str(tmp_path / "home"), "PYTHONPATH": str(root / "src"),
                "TELEGRAM_PLUGIN_PYTHON": sys.executable})
    result = subprocess.run([str(root / "bin/telegram-login"), "--status"],
                            env=env, capture_output=True, text=True, check=False, timeout=10)
    assert result.returncode == 0, result.stderr
    status = json.loads(result.stdout)
    assert status["credentials"] == "present" and status["authorized"] is False
    assert status["credential_source"] == "application"
    assert APP_HASH not in result.stdout + result.stderr and str(APP_ID) not in result.stdout


def test_builder_failure_does_not_echo_invalid_secret(tmp_path):
    source = tmp_path / ".env.publisher"
    source.write_text(f"TELEGRAM_API_ID={APP_HASH}\nTELEGRAM_API_HASH={APP_HASH}\n")
    result = subprocess.run([sys.executable, str(REPO / "scripts/build-release.py"),
                             "--credentials-file", str(source), "--output", str(tmp_path / "out")],
                            capture_output=True, text=True, check=False, timeout=10)
    assert result.returncode == 1
    assert APP_HASH not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr


def test_release_excludes_tracked_private_state_and_publisher_input(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("release_builder", REPO / "scripts/build-release.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    root = tmp_path / "repository"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for name in ["docs/.env", "docs/account.session", "skills/a/.env.publisher",
                 "skills/a/account.session-wal", "docs/auth-status.json"]:
        file = root / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("PRIVATE_STATE")
    publisher = root / "docs/publisher.md"
    publisher.write_text(f"TELEGRAM_API_ID={APP_ID}\nTELEGRAM_API_HASH={APP_HASH}\n")
    (root / "docs/public.md").write_text("PUBLIC_CONTROL")
    (root / ".env.example").write_text("TELEGRAM_API_ID=\nTELEGRAM_API_HASH=\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    monkeypatch.setattr(builder, "ROOT", root.resolve())
    archive_path = tmp_path / "release.tar.gz"
    builder.build(publisher, archive_path)
    with tarfile.open(archive_path) as archive:
        names = archive.getnames()
        assert names == ["telegram-plugin/.env.example", "telegram-plugin/docs/public.md",
                         "telegram-plugin/src/telegram_plugin/_application.json"]
        assert archive.extractfile("telegram-plugin/docs/public.md").read() == b"PUBLIC_CONTROL"
