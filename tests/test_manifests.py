import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MANIFESTS = (".claude-plugin", ".codex-plugin", ".cursor-plugin")


def _manifest(directory):
    return json.loads((REPO / directory / "plugin.json").read_text())


def _pyproject_version():
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    found = re.search(r'^version = "(.+)"$', text, re.MULTILINE)
    assert found, "pyproject.toml declares no version for these tests to compare against"
    return found[1]


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


def test_a_foreign_pyproject_is_not_trusted_for_the_version(monkeypatch, tmp_path):
    from telegram_plugin import server

    foreign = tmp_path / "pyproject.toml"
    foreign.write_text('[project]\nname = "something-else"\nversion = "9.9.9"\n', encoding="utf-8")
    monkeypatch.setattr(server, "_PYPROJECT", foreign)
    monkeypatch.setattr(server.metadata, "version", lambda name: "1.2.3")
    assert server.package_version() == "1.2.3"


def test_an_unreadable_pyproject_falls_back_to_metadata_instead_of_raising(monkeypatch, tmp_path):
    from telegram_plugin import server

    monkeypatch.setattr(server, "_PYPROJECT", tmp_path / "gone" / "pyproject.toml")
    monkeypatch.setattr(server.metadata, "version", lambda name: "1.2.3")
    assert server.package_version() == "1.2.3"


def test_a_pyproject_that_is_not_utf8_falls_back_instead_of_raising(monkeypatch, tmp_path):
    from telegram_plugin import server

    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_bytes(
        b'[project]\nname = "telegram-plugin"\nversion = "5.5.5"\ndescription = "\xff\xfe"\n'
    )
    monkeypatch.setattr(server, "_PYPROJECT", pyproject)
    monkeypatch.setattr(server.metadata, "version", lambda name: "1.2.3")
    assert server.package_version() == "1.2.3"


def test_an_unidentifiable_install_announces_a_version_instead_of_crashing(monkeypatch, tmp_path):
    from importlib import metadata

    from telegram_plugin import server

    def absent(name):
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(server, "_PYPROJECT", tmp_path / "gone" / "pyproject.toml")
    monkeypatch.setattr(server.metadata, "version", absent)
    assert server.package_version() == "0+unknown"


def test_the_version_survives_a_non_utf8_locale(tmp_path):
    # Read in the locale's encoding instead of UTF-8, this raises UnicodeDecodeError —
    # not an OSError — on the first non-ASCII byte, killing the server at startup.
    import os
    import subprocess
    import sys

    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "telegram-plugin"\ndescription = "длинное тире — вот"\nversion = "7.7.7"\n',
        encoding="utf-8",
    )
    probe = (
        "import locale, pathlib, sys\n"
        "from telegram_plugin import server\n"
        "server._PYPROJECT = pathlib.Path(sys.argv[1])\n"
        "print(locale.getpreferredencoding(False))\n"
        "print(server.package_version())\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe, str(pyproject)],
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "LC_ALL": "C",
            "LANG": "C",
            "PYTHONCOERCECLOCALE": "0",
            "PYTHONUTF8": "0",
            "PYTHONPATH": str(REPO / "src"),
        },
    )
    printed = result.stdout.split()
    if printed and printed[0].lower().replace("-", "") == "utf8":
        pytest.skip(f"this interpreter stayed on UTF-8 under LC_ALL=C ({printed[0]})")
    assert result.returncode == 0, result.stderr
    assert printed[1] == "7.7.7"


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
