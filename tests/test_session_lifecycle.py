"""Holding the account for a whole session was the real defect: a user-scope
plugin starts one server per Claude session, and the first one to touch Telegram
locked the account until that session died. Reconnecting costs ~280 ms, measured,
so there was never a reason to hold it."""

import asyncio
import multiprocessing as mp
import time

import pytest
from telethon.tl.types import User

from telegram_plugin.client import TelethonGateway, acquire_session_lock, session_lock
from telegram_plugin.config import load_config
from telegram_plugin.errors import SessionLocked
from telegram_plugin.refs import parse_chat_ref

REF = parse_chat_ref("@somechannel")


class CountingClient:
    """Counts what a real client would pay for on the wire."""

    def __init__(self):
        self.connects = 0
        self.disconnects = 0
        self.entity_lookups = 0

    async def connect(self):
        self.connects += 1

    async def is_user_authorized(self):
        return True

    async def disconnect(self):
        self.disconnects += 1

    async def get_entity(self, value):
        self.entity_lookups += 1
        # A real Telethon type: utils.get_peer_id and get_display_name are called
        # on whatever this returns, and a bare namespace does not satisfy them.
        return User(id=1, first_name="Chat", username="somechannel")

    async def get_me(self):
        return User(id=42, first_name="Test", username="tester")


def _gateway(tmp_path, client, **extra):
    config = load_config({
        "TELEGRAM_STATE_DIR": str(tmp_path),
        "TELEGRAM_API_ID": "1",
        "TELEGRAM_API_HASH": "h",
        **extra,
    })
    config.state_dir.mkdir(parents=True, exist_ok=True)
    config.session_path.write_bytes(b"")
    gateway = TelethonGateway(config)

    async def open_client():
        await client.connect()
        return client

    gateway._open_client = open_client
    return gateway, config


async def test_the_lock_is_released_once_the_gateway_goes_idle(tmp_path):
    client = CountingClient()
    gateway, config = _gateway(tmp_path, client, TELEGRAM_IDLE_TIMEOUT="0.05")
    await gateway.me()
    with pytest.raises(SessionLocked), session_lock(config.session_path):
        pass

    await asyncio.sleep(0.35)

    assert client.disconnects == 1, "the idle watcher must disconnect"
    with session_lock(config.session_path):
        pass  # another process can have the account now


async def test_work_after_an_idle_release_simply_reconnects(tmp_path):
    client = CountingClient()
    gateway, _ = _gateway(tmp_path, client, TELEGRAM_IDLE_TIMEOUT="0.05")
    await gateway.me()
    await asyncio.sleep(0.35)
    await gateway.me()
    assert client.connects == 2
    await gateway.close()


async def test_a_call_in_flight_is_never_disconnected_underneath_it(tmp_path):
    client = CountingClient()
    gateway, _ = _gateway(tmp_path, client, TELEGRAM_IDLE_TIMEOUT="0.05")

    async def slow_entity(value):
        await asyncio.sleep(0.3)
        client.entity_lookups += 1
        return User(id=1, first_name="Chat", username="somechannel")

    client.get_entity = slow_entity
    await gateway.resolve(REF)
    assert client.disconnects == 0, "an idle timer must not fire during a call"
    await gateway.close()


async def test_the_same_chat_is_resolved_once_per_connection(tmp_path):
    client = CountingClient()
    gateway, _ = _gateway(tmp_path, client)
    await gateway.resolve(REF)
    await gateway.resolve(REF)
    assert client.entity_lookups == 1, "resolving the same chat twice costs one round trip"
    await gateway.resolve(parse_chat_ref("@otherchannel"))
    assert client.entity_lookups == 2
    await gateway.close()


async def test_the_entity_cache_does_not_outlive_the_connection(tmp_path):
    client = CountingClient()
    gateway, _ = _gateway(tmp_path, client, TELEGRAM_IDLE_TIMEOUT="0.05")
    await gateway.resolve(REF)
    await asyncio.sleep(0.35)
    await gateway.resolve(REF)
    assert client.entity_lookups == 2, "entities belong to a client; a new client re-resolves"
    await gateway.close()


def _hold_then_release(path, ready, seconds):
    with session_lock(path):
        ready.set()
        time.sleep(seconds)


async def test_a_busy_session_is_waited_for_rather_than_refused(tmp_path):
    session = tmp_path / "telegram.session"
    ready = mp.Event()
    holder = mp.Process(target=_hold_then_release, args=(session, ready, 0.6))
    holder.start()
    try:
        assert ready.wait(10)
        started = time.monotonic()
        handle = await acquire_session_lock(session, timeout=10)
        waited = time.monotonic() - started
        assert waited >= 0.2, "it must actually have waited for the holder"
        handle.release()
    finally:
        holder.join(10)


async def test_waiting_gives_up_with_an_explanation(tmp_path):
    session = tmp_path / "telegram.session"
    ready = mp.Event()
    holder = mp.Process(target=_hold_then_release, args=(session, ready, 3))
    holder.start()
    try:
        assert ready.wait(10)
        with pytest.raises(SessionLocked) as excinfo:
            await acquire_session_lock(session, timeout=0.3)
        assert "another process" in str(excinfo.value).lower()
    finally:
        holder.join(10)


def test_the_new_settings_have_defaults_and_can_be_overridden():
    default = load_config({"HOME": "/tmp"})
    assert default.idle_timeout == 60.0
    assert default.lock_wait == 20.0
    tuned = load_config({"HOME": "/tmp", "TELEGRAM_IDLE_TIMEOUT": "5",
                         "TELEGRAM_LOCK_WAIT": "1.5"})
    assert tuned.idle_timeout == 5.0
    assert tuned.lock_wait == 1.5
