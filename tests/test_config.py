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


def test_an_empty_environment_value_does_not_fall_through_to_the_file():
    """A host that clears a variable has spoken. `or` read that as "unset" and let the
    file switch sending back on."""
    dotenv = "TELEGRAM_PLUGIN_ALLOW_SEND=1\nTELEGRAM_SESSION_NAME=from-file\n"
    cfg = load_config({"TELEGRAM_PLUGIN_ALLOW_SEND": "", "TELEGRAM_SESSION_NAME": ""}, dotenv)
    assert cfg.allow_send is False
    assert cfg.session_name == "telegram", "an empty value means the default, not the file"


def test_an_unset_variable_still_comes_from_the_file():
    """Positive control for the rule above: absence and emptiness are different."""
    cfg = load_config({}, "TELEGRAM_PLUGIN_ALLOW_SEND=1\n")
    assert cfg.allow_send is True


def test_a_ceiling_of_zero_is_honoured_rather_than_replaced_by_the_default():
    cfg = load_config({"TELEGRAM_PLUGIN_SEND_LIMIT": "0", "TELEGRAM_MAX_DOWNLOAD_BYTES": "0"})
    assert cfg.send_limit == 0
    assert cfg.max_download_bytes == 0


def test_a_negative_ceiling_clamps_to_the_strictest_rather_than_the_loosest():
    assert load_config({"TELEGRAM_PLUGIN_SEND_LIMIT": "-5"}).send_limit == 0


def test_an_unreadable_ceiling_defaults_and_is_named():
    from telegram_plugin.config import DEFAULT_MAX_DOWNLOAD_BYTES
    from telegram_plugin.log import config_summary

    cfg = load_config({"HOME": "/tmp", "TELEGRAM_MAX_DOWNLOAD_BYTES": "10MB"})
    assert cfg.max_download_bytes == DEFAULT_MAX_DOWNLOAD_BYTES
    assert cfg.unreadable == ("TELEGRAM_MAX_DOWNLOAD_BYTES",)
    assert "TELEGRAM_MAX_DOWNLOAD_BYTES" in config_summary(cfg)


def test_a_readable_configuration_names_nothing():
    assert load_config({"HOME": "/tmp", "TELEGRAM_PLUGIN_SEND_LIMIT": "3"}).unreadable == ()


def test_the_launcher_and_the_package_agree_on_the_telethon_floor():
    """The confinement guarantee rests on that number; two copies must not drift."""
    import re
    from pathlib import Path

    from telegram_plugin.config import DEPENDENCIES, MIN_TELETHON

    declared = re.search(r"telethon>=(\d+)\.(\d+)", " ".join(DEPENDENCIES))
    assert (int(declared[1]), int(declared[2])) == MIN_TELETHON
    launcher = (Path(__file__).resolve().parents[1] / "bin/telegram-mcp").read_text()
    assert f"({MIN_TELETHON[0]}, {MIN_TELETHON[1]})" in launcher
    assert f'MIN_TELETHON="{MIN_TELETHON[0]}.{MIN_TELETHON[1]}"' in launcher
