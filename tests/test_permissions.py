import stat

from telegram_plugin.client import ensure_state_dir
from telegram_plugin.config import load_config


def test_state_dir_is_private(tmp_path):
    config = load_config({"TELEGRAM_STATE_DIR": str(tmp_path / "state")})
    ensure_state_dir(config)
    assert stat.S_IMODE(config.state_dir.stat().st_mode) == 0o700


def test_existing_loose_state_dir_is_tightened(tmp_path):
    state = tmp_path / "state"
    state.mkdir(mode=0o755)
    config = load_config({"TELEGRAM_STATE_DIR": str(state)})
    ensure_state_dir(config)
    assert stat.S_IMODE(state.stat().st_mode) == 0o700


def test_dotenv_is_tightened_when_present(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    dotenv = state / ".env"
    dotenv.write_text("TELEGRAM_API_ID=1\n")
    dotenv.chmod(0o644)
    config = load_config({"TELEGRAM_STATE_DIR": str(state)})
    ensure_state_dir(config)
    assert stat.S_IMODE(dotenv.stat().st_mode) == 0o600


def test_output_root_is_created_private(tmp_path):
    config = load_config({"TELEGRAM_STATE_DIR": str(tmp_path / "state")})
    ensure_state_dir(config)
    assert stat.S_IMODE(config.output_root.stat().st_mode) == 0o700
