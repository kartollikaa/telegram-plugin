import multiprocessing as mp

import pytest

from telegram_plugin.client import session_lock
from telegram_plugin.errors import SessionLocked


def _hold(path, ready, release):
    with session_lock(path):
        ready.set()
        release.wait(10)


def test_second_holder_gets_explanation(tmp_path):
    path = tmp_path / "telegram.session"
    ready, release = mp.Event(), mp.Event()
    holder = mp.Process(target=_hold, args=(path, ready, release))
    holder.start()
    try:
        assert ready.wait(10)
        with pytest.raises(SessionLocked) as excinfo, session_lock(path):
            pass
        assert "another process" in str(excinfo.value).lower()
    finally:
        release.set()
        holder.join(10)


def test_lock_is_released_after_the_block(tmp_path):
    path = tmp_path / "telegram.session"
    with session_lock(path):
        pass
    with session_lock(path):
        pass


def test_lock_file_is_not_world_readable(tmp_path):
    import stat

    path = tmp_path / "telegram.session"
    with session_lock(path):
        mode = stat.S_IMODE((tmp_path / "telegram.session.lock").stat().st_mode)
    assert mode == 0o600


async def test_the_lock_is_released_when_the_client_cannot_be_opened(tmp_path):
    """A failed connect used to leave the lock held, after which every later call
    in the same process blamed a non-existent other process."""
    from telegram_plugin.client import TelethonGateway
    from telegram_plugin.config import load_config
    from telegram_plugin.errors import NotAuthorized

    config = load_config(
        {"TELEGRAM_STATE_DIR": str(tmp_path), "TELEGRAM_API_ID": "1", "TELEGRAM_API_HASH": "h"}
    )
    config.state_dir.mkdir(parents=True, exist_ok=True)
    config.session_path.write_bytes(b"")
    gateway = TelethonGateway(config)

    async def refuse():
        raise NotAuthorized()

    gateway._open_client = refuse

    for _ in range(3):
        with pytest.raises(NotAuthorized):
            await gateway._connected()

    with session_lock(config.session_path):
        pass
