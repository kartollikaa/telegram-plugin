"""Download limits and filename hygiene, against a stub of the two Telethon
methods the gateway uses for downloads."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from telegram_plugin.client import TelethonGateway
from telegram_plugin.config import load_config
from telegram_plugin.errors import EscapedOutput, MediaTooLarge, NoSuchMedia
from telegram_plugin.refs import parse_chat_ref
from tests.telethon_doubles import document_message, photo_message

REF = parse_chat_ref("@somechannel")


class StubClient:
    def __init__(self, size: int, file_name: str) -> None:
        self.message = document_message(7, name=file_name, size=size)
        self.file_name = file_name
        self.escape_to: Path | None = None

    async def get_messages(self, entity, ids):
        return [self.message if ids[0] == 7 else None]

    async def download_media(self, message, file):
        target = self.escape_to or Path(file) / self.file_name
        target.write_bytes(b"%PDF-1.4")
        return str(target)


def _gateway(tmp_path, size=1024, file_name="report.pdf", **env):
    config = load_config({"TELEGRAM_STATE_DIR": str(tmp_path), **env})
    gateway = TelethonGateway(config)
    client = StubClient(size, file_name)
    gateway._client = client

    async def entity_of(ref):
        return SimpleNamespace(username="somechannel", id=1)

    gateway._entity = entity_of
    return gateway, config


async def test_a_normal_attachment_downloads(tmp_path):
    gateway, config = _gateway(tmp_path)
    path = await gateway.download(REF, 7, config.output_root)
    assert Path(path).exists()
    assert Path(path).name == "report.pdf"


async def test_an_attachment_over_the_ceiling_is_refused_before_downloading(tmp_path):
    gateway, config = _gateway(tmp_path, size=200 * 1024 * 1024)
    with pytest.raises(MediaTooLarge) as excinfo:
        await gateway.download(REF, 7, config.output_root)
    assert "209715200" in str(excinfo.value)
    assert list(config.output_root.glob("*")) == []


async def test_the_ceiling_is_configurable(tmp_path):
    gateway, config = _gateway(tmp_path, size=2048, TELEGRAM_MAX_DOWNLOAD_BYTES="1024")
    with pytest.raises(MediaTooLarge):
        await gateway.download(REF, 7, config.output_root)


# Telethon >= 1.42 basenames a sender-supplied name before it reaches us, so the
# cases here are the ones that survive that: shell metacharacters, not separators.
# Separator handling is covered directly on safe_name in tests/test_render.py.
@pytest.mark.parametrize(
    "hostile,expected",
    [
        ("; rm -rf ~ ;.pdf", "_rm_-rf_.pdf"),
        ("$(id)-report.pdf", "_id_-report.pdf"),
        ("with space.pdf", "with_space.pdf"),
        ("x\n; id.pdf", "x_id.pdf"),
    ],
)
async def test_a_sender_chosen_filename_is_sanitised(tmp_path, hostile, expected):
    gateway, config = _gateway(tmp_path, file_name=hostile)
    path = Path(await gateway.download(REF, 7, config.output_root))
    assert path.name == expected
    assert path.parent == config.output_root.resolve()


async def test_a_message_without_media_says_so(tmp_path):
    gateway, config = _gateway(tmp_path)
    gateway._client.message = SimpleNamespace(id=7, media=None)
    with pytest.raises(NoSuchMedia):
        await gateway.download(REF, 7, config.output_root)


async def test_a_photo_is_measured_against_the_ceiling_too(tmp_path):
    """The media wrapper has no size, so reading it there let every photo past the cap."""
    gateway, config = _gateway(tmp_path, TELEGRAM_MAX_DOWNLOAD_BYTES="100")
    gateway._client.message = photo_message(7, size=5000)
    with pytest.raises(MediaTooLarge):
        await gateway.download(REF, 7, config.output_root)


async def test_a_ceiling_of_zero_refuses_everything_instead_of_defaulting(tmp_path):
    gateway, config = _gateway(tmp_path, size=1, TELEGRAM_MAX_DOWNLOAD_BYTES="0")
    with pytest.raises(MediaTooLarge):
        await gateway.download(REF, 7, config.output_root)


async def test_a_file_that_lands_outside_the_destination_is_refused(tmp_path):
    """Telethon >= 1.42 basenames a sender-supplied name; this is the guard for when it does not."""
    gateway, config = _gateway(tmp_path)
    escaped = tmp_path / "escaped.pdf"
    gateway._client.escape_to = escaped
    with pytest.raises(EscapedOutput) as excinfo:
        await gateway.download(REF, 7, config.output_root)
    assert str(escaped) in str(excinfo.value)


async def test_the_confinement_guard_passes_the_ordinary_case(tmp_path):
    """A guard that refuses everything would pass the test above and break the plugin."""
    gateway, config = _gateway(tmp_path)
    path = Path(await gateway.download(REF, 7, config.output_root))
    assert path.parent == config.output_root.resolve()
