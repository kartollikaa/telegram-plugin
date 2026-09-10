from pathlib import Path

from telegram_plugin.config import load_config, parse_dotenv


def test_parse_dotenv_ignores_comments_and_blanks():
    assert parse_dotenv('# c\n\nA=1\nB="two"\nnonsense\n') == {"A": "1", "B": "two"}


def test_real_env_wins_over_dotenv():
    cfg = load_config({"TELEGRAM_API_HASH": "from-env"}, "TELEGRAM_API_HASH=from-file\n")
    assert cfg.api_hash == "from-env"


def test_dotenv_wins_over_default():
    cfg = load_config({}, "TELEGRAM_SESSION_NAME=custom\n")
    assert cfg.session_name == "custom"


def test_default_state_dir_is_xdg_like(tmp_path):
    cfg = load_config({"HOME": str(tmp_path)})
    assert cfg.state_dir == tmp_path / ".local/state/telegram-plugin"


def test_allow_send_is_off_unless_exactly_one():
    assert load_config({}).allow_send is False
    assert load_config({"TELEGRAM_PLUGIN_ALLOW_SEND": "0"}).allow_send is False
    assert load_config({"TELEGRAM_PLUGIN_ALLOW_SEND": "true"}).allow_send is False
    assert load_config({"TELEGRAM_PLUGIN_ALLOW_SEND": "1"}).allow_send is True


def test_session_path_derives_from_state_dir_and_name():
    cfg = load_config({"TELEGRAM_STATE_DIR": "/s", "TELEGRAM_SESSION_NAME": "n"})
    assert cfg.session_path == Path("/s/n.session")


def test_api_id_is_an_int_when_present():
    assert load_config({"TELEGRAM_API_ID": "12345"}).api_id == 12345
    assert load_config({}).api_id is None
