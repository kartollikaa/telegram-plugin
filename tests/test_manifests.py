"""Packaging contract: one portable manifest, one Claude compatibility manifest,
and a runtime that installs Telethon and nothing else."""

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PORTABLE = REPO / "plugin.json"
CLAUDE = REPO / ".claude-plugin" / "plugin.json"
SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"

# Agent Plugins 1.0.0 sets additionalProperties:false, so anything outside this
# set makes the manifest invalid. Vendored rather than fetched: the suite runs
# without a network.
SCHEMA_PROPERTIES = {
    "$schema",
    "name",
    "version",
    "description",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
    "extensions",
}


def _manifest(path: Path) -> dict:
    return json.loads(path.read_text())


def _package_version() -> str:
    return re.search(
        r'^version = "(.*)"$', (REPO / "pyproject.toml").read_text(), re.MULTILINE
    )[1]


def test_portable_manifest_is_canonical():
    manifest = _manifest(PORTABLE)
    assert manifest["$schema"] == SCHEMA
    assert manifest["name"] == "telegram"
    assert manifest["version"] == "0.6.0"
    assert manifest["description"]
    # A portable package always discovers skills/ itself; a declaration here
    # cannot add to or override that, so claiming one would only mislead.
    assert "skills" not in manifest
    assert "mcp" not in json.dumps(manifest).lower()


def test_portable_manifest_stays_inside_the_schema():
    unknown = set(_manifest(PORTABLE)) - SCHEMA_PROPERTIES
    assert unknown == set(), f"not allowed by the schema: {sorted(unknown)}"


def test_portable_manifest_carries_openai_install_metadata():
    interface = _manifest(PORTABLE)["extensions"]["com.openai"]["interface"]
    assert interface["displayName"]
    assert interface["shortDescription"]


def test_claude_manifest_is_skills_only():
    manifest = _manifest(CLAUDE)
    assert manifest["name"] == "telegram"
    assert manifest["skills"] == "./skills/"
    assert "mcpServers" not in manifest
    assert "mcp" not in json.dumps(manifest).lower()


def test_versions_agree():
    versions = {
        _package_version(),
        _manifest(PORTABLE)["version"],
        _manifest(CLAUDE)["version"],
    }
    assert versions == {"0.6.0"}, versions


def test_runtime_dependencies_are_telethon_only():
    from telegram_plugin.config import DEPENDENCIES

    assert all(name.startswith("telethon") for name in DEPENDENCIES), DEPENDENCIES

    dependencies = re.search(
        r"^dependencies = \[(.*?)\]",
        (REPO / "pyproject.toml").read_text(),
        re.MULTILINE | re.DOTALL,
    )[1]
    assert "telethon" in dependencies
    assert "mcp" not in dependencies.lower()


def test_the_compatibility_manifests_for_unverified_hosts_are_gone():
    """They were written from documentation and never run against a real host.
    An absolute-path CLI invocation works everywhere and claims nothing."""
    for directory in (".codex-plugin", ".cursor-plugin"):
        assert not (REPO / directory).exists(), directory


def test_the_launchers_exist_and_are_executable():
    for name in ("bin/telegram", "bin/telegram-login"):
        launcher = REPO / name
        assert launcher.exists(), name
        assert launcher.stat().st_mode & 0o111, name


def test_no_mcp_launcher_remains():
    assert not (REPO / "bin/telegram-mcp").exists()


def test_launcher_dependency_list_matches_the_package():
    from telegram_plugin.config import DEPENDENCIES

    launcher = (REPO / "bin/telegram").read_text()
    for dependency in DEPENDENCIES:
        assert dependency in launcher


def test_no_personal_paths_in_manifests():
    for path in (PORTABLE, CLAUDE):
        assert "/Users/" not in path.read_text(), str(path)
