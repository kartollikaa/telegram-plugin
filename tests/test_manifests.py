import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MANIFESTS = (".claude-plugin", ".codex-plugin", ".cursor-plugin")


def _manifest(directory):
    return json.loads((REPO / directory / "plugin.json").read_text())


@pytest.mark.parametrize("directory", MANIFESTS)
def test_manifest_is_valid_json_named_telegram(directory):
    assert _manifest(directory)["name"] == "telegram"


@pytest.mark.parametrize("directory", MANIFESTS)
def test_manifest_declares_the_skills_directory(directory):
    assert _manifest(directory)["skills"] == "./skills/"


def test_all_manifests_point_at_one_launcher():
    tails = {
        _manifest(d)["mcpServers"]["telegram"]["command"].split("}")[-1] for d in MANIFESTS
    }
    assert tails == {"/bin/telegram-mcp"}


def test_all_manifests_agree_on_version():
    assert len({_manifest(d)["version"] for d in MANIFESTS}) == 1


def test_the_launcher_they_point_at_exists_and_is_executable():
    launcher = REPO / "bin/telegram-mcp"
    assert launcher.exists()
    assert launcher.stat().st_mode & 0o111


def test_launcher_dependency_list_matches_the_package():
    from telegram_plugin.config import DEPENDENCIES

    launcher = (REPO / "bin/telegram-mcp").read_text()
    for dependency in DEPENDENCIES:
        assert dependency in launcher


@pytest.mark.parametrize("directory", MANIFESTS)
def test_no_personal_paths_in_manifests(directory):
    assert "/Users/" not in (REPO / directory / "plugin.json").read_text()
