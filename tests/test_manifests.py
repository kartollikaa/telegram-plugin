import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MANIFESTS = (".claude-plugin", ".codex-plugin", ".cursor-plugin")


def _manifest(directory):
    return json.loads((REPO / directory / "plugin.json").read_text())


def _pyproject_version():
    return re.search(r'^version = "(.+)"$', (REPO / "pyproject.toml").read_text(), re.MULTILINE)[1]


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


def test_the_server_announces_the_package_version():
    from telegram_plugin.config import load_config
    from telegram_plugin.server import build_server
    from tests.fakes import FakeGateway

    server = build_server(load_config({"HOME": "/tmp"}), FakeGateway())
    assert server.version == _pyproject_version()


def test_the_announced_version_ignores_stale_installed_metadata(monkeypatch):
    from telegram_plugin import server

    monkeypatch.setattr(server.metadata, "version", lambda name: "0.1.0")
    assert server.package_version() == _pyproject_version()


def test_the_announced_version_falls_back_to_metadata_outside_a_source_tree(monkeypatch):
    from telegram_plugin import server

    monkeypatch.setattr(server, "_version_from_source_tree", lambda: None)
    monkeypatch.setattr(server.metadata, "version", lambda name: "1.2.3")
    assert server.package_version() == "1.2.3"


def test_an_unidentifiable_install_announces_a_version_instead_of_crashing(monkeypatch):
    from importlib import metadata

    from telegram_plugin import server

    def absent(name):
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(server, "_version_from_source_tree", lambda: None)
    monkeypatch.setattr(server.metadata, "version", absent)
    assert server.package_version() == "0+unknown"


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
