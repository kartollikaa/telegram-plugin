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
