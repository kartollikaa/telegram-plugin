"""Holding the account for a whole session was the real defect: a user-scope
plugin starts one server per Claude session, and the first one to touch Telegram
locked the account until that session died. Reconnecting costs ~280 ms, measured,
so there was never a reason to hold it."""

import asyncio
import errno
import multiprocessing as mp
import time
from types import SimpleNamespace

import pytest
from telethon.tl.types import User

from telegram_plugin.client import TelethonGateway, acquire_session_lock, session_lock
from telegram_plugin.config import load_config
from telegram_plugin.errors import SessionLocked
from telegram_plugin.refs import parse_chat_ref

REF = parse_chat_ref("@somechannel")


async def until(condition, seconds=5.0, note=""):
    """Wait for a condition instead of guessing how long the scheduler needs.

    A fixed sleep racing a 50 ms timeout flakes on a loaded runner, and the failure
    reads like a lock-lifecycle regression rather than a slow machine.
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(note or "condition never became true")


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
    await until(lambda: client.disconnects == 1)
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
    await until(lambda: client.disconnects == 1)
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


class BlockingDisconnectClient(CountingClient):
    """Lets a test hold the gateway inside close()'s disconnect await."""

    def __init__(self):
        super().__init__()
        self.disconnect_started = asyncio.Event()
        self.may_finish = asyncio.Event()

    async def disconnect(self):
        self.disconnects += 1
        self.disconnect_started.set()
        await self.may_finish.wait()


async def test_a_reconnect_during_close_still_ends_up_releasable(tmp_path):
    """Pins the ordering inside close() that makes this safe.

    A call arriving while close() awaits disconnect() must end up with a connection
    something will release. What protects it is that the lock is released *last*:
    the reconnect blocks on the lock until close is finished, so it never sees a
    half-closed gateway. Move the release before the disconnect and this test goes
    red — the reconnect gets the account while the old watcher is still winding
    down, and nothing arms a new one.
    """
    client = BlockingDisconnectClient()
    gateway, _ = _gateway(tmp_path, client, TELEGRAM_IDLE_TIMEOUT="0.05")
    await gateway.me()

    await until(client.disconnect_started.is_set, note="the idle watcher must start closing")
    reconnect = asyncio.create_task(gateway.me())
    await asyncio.sleep(0.05)  # let the reconnect reach the lock
    client.may_finish.set()
    await reconnect

    assert client.connects == 2
    await until(
        lambda: client.disconnects == 2,
        note="the reconnected client was left with no idle watcher",
    )
    await gateway.close()


async def test_closing_from_outside_cancels_a_sleeping_watcher(tmp_path):
    client = CountingClient()
    gateway, _ = _gateway(tmp_path, client, TELEGRAM_IDLE_TIMEOUT="30")
    await gateway.me()
    watcher = gateway._idle_watcher
    assert watcher is not None and not watcher.done()

    await gateway.close()

    await until(watcher.done, note="close() must not leave the watcher pending")
    assert gateway._idle_watcher is None


async def test_a_lock_error_that_is_not_contention_is_not_disguised_as_it(tmp_path, monkeypatch):
    """ENOLCK reported as "held by another process" sends the operator hunting a
    process that does not exist."""
    import fcntl

    from telegram_plugin.client import _try_session_lock

    def refuse(descriptor, operation):
        raise OSError(errno.ENOLCK, "no locks available")

    monkeypatch.setattr(fcntl, "flock", refuse)
    with pytest.raises(OSError) as excinfo:
        _try_session_lock(tmp_path / "telegram.session")
    assert not isinstance(excinfo.value, SessionLocked)
    assert excinfo.value.errno == errno.ENOLCK


async def test_a_busy_lock_is_still_reported_as_busy(tmp_path, monkeypatch):
    import fcntl

    from telegram_plugin.client import _try_session_lock

    def busy(descriptor, operation):
        raise OSError(errno.EAGAIN, "would block")

    monkeypatch.setattr(fcntl, "flock", busy)
    assert _try_session_lock(tmp_path / "telegram.session") is None


class InviteClient(CountingClient):
    def __init__(self):
        super().__init__()
        self.invite_checks = 0

    async def __call__(self, request):
        self.invite_checks += 1
        return SimpleNamespace(chat=User(id=5, first_name="Invited"))


async def test_an_invite_is_re_checked_rather_than_cached(tmp_path):
    """Resolving an invite answers "is this account a member?" — an answer that
    changes when the operator joins or leaves."""
    client = InviteClient()
    gateway, _ = _gateway(tmp_path, client)
    invite = parse_chat_ref("https://t.me/+AbCdEf")
    await gateway.resolve(invite)
    await gateway.resolve(invite)
    assert client.invite_checks == 2
    await gateway.close()


def test_a_negative_timeout_falls_back_instead_of_disabling_the_release():
    negative = load_config({"HOME": "/tmp", "TELEGRAM_IDLE_TIMEOUT": "-5",
                            "TELEGRAM_LOCK_WAIT": "-1"})
    assert negative.idle_timeout == 60.0
    assert negative.lock_wait == 20.0
    off = load_config({"HOME": "/tmp", "TELEGRAM_IDLE_TIMEOUT": "0"})
    assert off.idle_timeout == 0.0, "zero stays a deliberate opt-out"


async def test_the_server_hands_the_account_back_on_shutdown(tmp_path):
    """Without this the process exits holding the account, and the operator's other
    sessions keep being told it is busy by a server that no longer exists."""
    from telegram_plugin.server import closing_lifespan

    client = CountingClient()
    gateway, _ = _gateway(tmp_path, client, TELEGRAM_IDLE_TIMEOUT="30")
    await gateway.me()
    assert client.disconnects == 0

    async with closing_lifespan(gateway)(None):
        pass

    assert client.disconnects == 1
    with session_lock(gateway._config.session_path):
        pass  # the lock came back with it
