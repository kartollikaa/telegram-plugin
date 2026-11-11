import json
import os
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telethon.tl.types import User

from telegram_plugin.client import TelethonGateway
from telegram_plugin.config import load_config
from telegram_plugin.paths import open_confined_file
from telegram_plugin.refs import parse_chat_ref
from tests.fakes import FakeGateway
from tests.test_cli import _invoke

PNG = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR' + b'PNG DATA'


class MediaGateway(FakeGateway):
    def __init__(self):
        super().__init__()
        self.uploads = []
        self.accesses = 0

    def _check(self):
        self.accesses += 1
        super()._check()

    async def send_media(self, ref, stream, *, media_type, caption='', reply_to=None):
        self._check()
        self.uploads.append((ref.value, stream.name, stream.read(), media_type, caption, reply_to))
        return {'message_id': 999, 'chat_id': -1001, 'chat_title': 'Alpha',
                'reply_to': reply_to, 'media_type': media_type}


def invoke(capsys, tmp_path, args, *, enabled=True, gateway=None):
    gateway = gateway or MediaGateway()
    code, captured, _ = _invoke(
        capsys, tmp_path, ['send', *args], gateway,
        {'TELEGRAM_PLUGIN_ALLOW_SEND': '1' if enabled else '0'},
    )
    return code, captured, gateway


@pytest.mark.parametrize('kind', ['photo', 'document'])
@pytest.mark.parametrize('caption_mode', ['none', 'inline', 'file'])
@pytest.mark.parametrize('reply_to', [None, 12])
def test_media_dispatch(capsys, tmp_path, kind, caption_mode, reply_to):
    root = tmp_path / 'output'
    root.mkdir()
    (root / 'image.png').write_bytes(PNG)
    args = ['@alpha', '--' + kind, 'image.png']
    caption = '' if caption_mode == 'none' else 'literal **caption**; $(never run)\nnext line'
    if caption_mode == 'inline':
        args += ['--caption', caption]
    elif caption_mode == 'file':
        (root / 'caption.txt').write_text(caption)
        args += ['--caption-file', 'caption.txt']
    if reply_to:
        args += ['--reply-to', str(reply_to)]
    code, captured, gateway = invoke(capsys, tmp_path, args)
    assert code == 0
    assert captured.err == ''
    assert json.loads(captured.out) == {'message_id': 999, 'chat_id': -1001,
        'chat_title': 'Alpha', 'reply_to': reply_to, 'media_type': kind}
    assert gateway.uploads == [('alpha', 'image.png', PNG, kind, caption, reply_to)]
    assert gateway.close_calls == 1


@pytest.mark.parametrize('kind', ['file', 'photo', 'document'])
def test_disabled_before_file_read_or_recipient_resolution(capsys, tmp_path, kind, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('file access')
    monkeypatch.setattr('telegram_plugin.application.open_confined_file', forbidden)
    monkeypatch.setattr('telegram_plugin.cli.read_confined_text', forbidden)
    code, captured, gateway = invoke(capsys, tmp_path,
        ['not a reference!', '--' + kind, 'missing.png', '--caption-file', 'missing.txt'], enabled=False)
    assert code == 1
    assert json.loads(captured.out)['error']['code'] == 'send_disabled'
    assert gateway.uploads == []
    assert gateway.accesses == 0


@pytest.mark.parametrize('kind', ['missing', 'directory', 'symlink', 'parent-symlink',
                                  'outside', 'escape', 'fifo', 'unreadable'])
@pytest.mark.parametrize('option', ['--file', '--document'])
def test_unsafe_media_never_reaches_gateway(capsys, tmp_path, kind, monkeypatch, option):
    root = tmp_path / 'output'
    root.mkdir()
    (root / 'ok.bin').write_bytes(b'OK')
    outside = tmp_path / 'outside.bin'
    outside.write_bytes(b'OUTSIDE')
    candidate = 'missing.bin'
    if kind == 'directory':
        candidate = 'dir'
        (root / candidate).mkdir()
    elif kind == 'symlink':
        candidate = 'link'
        (root / candidate).symlink_to(outside)
    elif kind == 'parent-symlink':
        (root / 'nested').symlink_to(tmp_path)
        candidate = 'nested/outside.bin'
    elif kind == 'outside':
        candidate = str(outside)
    elif kind == 'escape':
        candidate = '../outside.bin'
    elif kind == 'fifo':
        candidate = 'pipe'
        os.mkfifo(root / candidate)
    elif kind == 'unreadable':
        original_open = os.open
        def deny(path, *args, **kwargs):
            if path == 'denied.bin':
                raise PermissionError('private path')
            return original_open(path, *args, **kwargs)
        monkeypatch.setattr(os, 'open', deny)
        candidate = 'denied.bin'
    code, captured, gateway = invoke(capsys, tmp_path, ['@alpha', option, candidate])
    assert code == 1
    assert json.loads(captured.out)['error']['code'] == 'unsafe_path'
    assert gateway.uploads == []
    assert gateway.accesses == 0
    code, _, gateway = invoke(capsys, tmp_path, ['@alpha', '--document', 'ok.bin'])
    assert code == 0
    assert gateway.uploads[0][2] == b'OK'


@pytest.mark.parametrize('args', [
    ['--photo', 'a.png', '--document', 'b.pdf'],
    ['--file', 'a.png', '--document', 'b.pdf'],
    ['--file', 'a.png', '--text', 'hi'],
    ['--text', 'hi', '--photo', 'a.png'],
    ['--document', 'a', '--caption', 'hi', '--caption-file', 'caption'],
    ['--document', 'a', '--reply-to', '0'],
])
def test_invalid_combinations(capsys, tmp_path, args):
    code, captured, gateway = invoke(capsys, tmp_path, ['@alpha', *args])
    assert code == 2
    assert 'usage:' in captured.err
    assert gateway.uploads == []
    assert gateway.close_calls == 0


@pytest.mark.parametrize('args', [
    ['--text', 'hi', '--caption', 'invalid'],
    ['--text-file', 'missing.txt', '--caption-file', 'missing.txt'],
    ['--photo', 'file.pdf'],
])
def test_semantic_argument_errors(capsys, tmp_path, args):
    root = tmp_path / 'output'
    root.mkdir()
    (root / 'file.pdf').write_bytes(b'PDF')
    code, captured, gateway = invoke(capsys, tmp_path, ['@alpha', *args])
    assert code == 1
    assert json.loads(captured.out)['error']['code'] == 'invalid_send_arguments'
    assert gateway.uploads == []


@pytest.mark.parametrize('kind', ['photo', 'document'])
async def test_telethon_media_call_uses_locked_session_and_confirmed_response(tmp_path, kind):
    config = load_config({'TELEGRAM_STATE_DIR': str(tmp_path)})
    gateway = TelethonGateway(config)
    entity = User(id=42, first_name='Recipient')
    client = SimpleNamespace(send_file=AsyncMock(return_value=SimpleNamespace(
        id=123, photo=object() if kind == 'photo' else None,
        document=object() if kind == 'document' else None)))
    states = []
    @asynccontextmanager
    async def session():
        states.append('locked')
        yield client
        states.append('released')
    gateway._session = session
    async def resolve(ref):
        assert states == ['locked']
        assert ref.value == 'alpha'
        return entity
    gateway._entity = resolve
    (tmp_path / 'image.png').write_bytes(b'IMAGE')
    with open_confined_file('image.png', root=tmp_path) as stream:
        result = await gateway.send_media(parse_chat_ref('@alpha'), stream,
            media_type=kind, caption='literal **caption**', reply_to=7)
        client.send_file.assert_awaited_once_with(entity, stream, caption='literal **caption**',
            reply_to=7, force_document=kind == 'document', parse_mode=None)
    assert states == ['locked', 'released']
    assert result == {'message_id': 123, 'chat_id': 42, 'chat_title': 'Recipient',
                      'reply_to': 7, 'media_type': kind}


def test_descriptor_remains_bound_when_path_is_replaced(tmp_path):
    file = tmp_path / 'file.bin'
    file.write_bytes(b'APPROVED')
    with open_confined_file('file.bin', root=tmp_path) as stream:
        file.unlink()
        file.write_bytes(b'REPLACEMENT')
        assert stream.read() == b'APPROVED'
    with open_confined_file('file.bin', root=tmp_path) as stream:
        assert stream.read() == b'REPLACEMENT'


def test_failed_upload_is_not_retried(capsys, tmp_path):
    class FailingGateway(MediaGateway):
        async def send_media(self, *args, **kwargs):
            self._check()
            raise OSError('transport failed')
    root = tmp_path / 'output'
    root.mkdir()
    (root / 'file.bin').write_bytes(b'DATA')
    gateway = FailingGateway()
    code, captured, _ = invoke(capsys, tmp_path, ['@alpha', '--document', 'file.bin'], gateway=gateway)
    error = json.loads(captured.out)['error']
    assert code == 1
    assert error['code'] == 'unexpected_error'
    assert error['retryable'] is False
    assert gateway.accesses == 1
    assert gateway.close_calls == 1


@pytest.mark.parametrize('kind', ['photo', 'document'])
async def test_real_telethon_conversion_preserves_explicit_media_type(tmp_path, kind):
    from telethon import TelegramClient
    from telethon.sessions import MemorySession
    from telethon.tl import types
    client = TelegramClient(MemorySession(), 1, 'test')
    client.upload_file = AsyncMock(return_value=types.InputFile(1, 1, 'image.png', ''))
    (tmp_path / 'image.png').write_bytes(b'IMAGE')
    with open_confined_file('image.png', root=tmp_path) as stream:
        _, media, _ = await client._file_to_media(stream, force_document=kind == 'document')
    expected = types.InputMediaUploadedPhoto if kind == 'photo' else types.InputMediaUploadedDocument
    assert isinstance(media, expected)
    client.upload_file.assert_awaited_once()
    if kind == 'document':
        assert any(isinstance(a, types.DocumentAttributeFilename) and a.file_name == 'image.png'
                   for a in media.attributes)


async def test_gateway_disables_rpc_retry_and_automatic_flood_sleep(tmp_path, monkeypatch):
    config = load_config({'TELEGRAM_STATE_DIR': str(tmp_path),
                          'TELEGRAM_API_ID': '1', 'TELEGRAM_API_HASH': 'test'})
    client = SimpleNamespace(connect=AsyncMock(), is_user_authorized=AsyncMock(return_value=True))
    created = []
    def factory(*args, **kwargs):
        created.append(kwargs)
        return client
    monkeypatch.setattr('telegram_plugin.client.TelegramClient', factory)
    assert await TelethonGateway(config)._open_client() is client
    assert created == [{'request_retries': 0, 'flood_sleep_threshold': 0,
                       'raise_last_call_error': True, 'auto_reconnect': False}]


async def test_transport_policy_prevents_replaying_unacknowledged_requests(tmp_path, monkeypatch):
    from telethon import TelegramClient
    from telethon.sessions import MemorySession

    async def requeued_after_disconnect(client):
        sender = client._sender
        sender._user_connected = True
        sender._connection = SimpleNamespace(disconnect=AsyncMock())
        sender._connect = AsyncMock()
        sender._disconnect = AsyncMock()
        sender._auto_reconnect_callback = None
        pending = object()
        sender._pending_state = {123: pending}
        requeued = []
        sender._send_queue = SimpleNamespace(extend=lambda states: requeued.extend(states))
        await sender._reconnect(OSError('simulated lost acknowledgement'))
        return requeued, sender._connect.await_count, sender._disconnect.await_count

    control = TelegramClient(MemorySession(), 1, 'test', auto_reconnect=True)
    replayed, connections, disconnects = await requeued_after_disconnect(control)
    assert len(replayed) == 1
    assert (connections, disconnects) == (1, 0)

    def factory(*args, **kwargs):
        client = TelegramClient(MemorySession(), 1, 'test', **kwargs)
        client.connect = AsyncMock()
        client.is_user_authorized = AsyncMock(return_value=True)
        return client

    monkeypatch.setattr('telegram_plugin.client.TelegramClient', factory)
    config = load_config({'TELEGRAM_STATE_DIR': str(tmp_path),
                          'TELEGRAM_API_ID': '1', 'TELEGRAM_API_HASH': 'test'})
    client = await TelethonGateway(config)._open_client()
    replayed, connections, disconnects = await requeued_after_disconnect(client)
    assert replayed == []
    assert (connections, disconnects) == (0, 1)


@pytest.mark.parametrize(('name', 'content', 'expected', 'sent_name'), [
    ('image.png', PNG, 'photo', 'image.png'),
    ('no-extension', PNG, 'photo', 'no-extension.png'),
    ('image.jpeg', b'\xff\xd8\xff\xe0JPEG DATA', 'photo', 'image.jpeg'),
    ('data.bin', b'BINARY DATA', 'document', 'data.bin'),
    ('fake.png', b'NOT AN IMAGE', 'document', 'fake.png'),
    ('animation.gif', b'GIF89a', 'document', 'animation.gif'),
    ('vector.svg', b'<svg/>', 'document', 'vector.svg'),
    ('empty', b'', 'document', 'empty'),
])
def test_auto_media_from_content(capsys, tmp_path, name, content, expected, sent_name):
    root = tmp_path / 'output'
    root.mkdir()
    (root / name).write_bytes(content)
    code, captured, gateway = invoke(capsys, tmp_path,
        ['@alpha', '--file', name, '--caption', 'Caption', '--reply-to', '7'])
    assert code == 0
    assert json.loads(captured.out)['media_type'] == expected
    assert gateway.uploads == [('alpha', sent_name, content, expected, 'Caption', 7)]


def test_explicit_document_overrides_auto_photo(capsys, tmp_path):
    root = tmp_path / 'output'
    root.mkdir()
    (root / 'picture.png').write_bytes(PNG)
    code, captured, gateway = invoke(capsys, tmp_path, ['@alpha', '--document', 'picture.png'])
    assert code == 0
    assert json.loads(captured.out)['media_type'] == 'document'
    assert gateway.uploads[0][1:4] == ('picture.png', PNG, 'document')


@pytest.mark.parametrize('offset', [0, 3])
def test_photo_sniff_preserves_stream_position(offset):
    from io import BytesIO

    from telegram_plugin.media import photo_format
    stream = BytesIO(b'xxx'[:offset] + PNG)
    stream.seek(offset)
    assert photo_format(stream) == 'png'
    assert stream.tell() == offset
    assert stream.read() == PNG
