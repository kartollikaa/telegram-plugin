from telegram_plugin.errors import LOGIN_HINT, NotAuthorized, SessionLocked, UnsafePath, describe


class FloodWaitError(Exception):
    def __init__(self, seconds):
        super().__init__(f"wait {seconds}")
        self.seconds = seconds


def test_flood_wait_reports_seconds_and_forbids_retry():
    message = describe(FloodWaitError(42))
    assert "42" in message
    assert "do not retry immediately" in message.lower()


def test_not_authorized_names_the_login_command():
    message = describe(NotAuthorized())
    assert "telegram-login" in message
    assert "Traceback" not in message


def test_session_locked_explains_the_conflict():
    message = describe(SessionLocked("/state/telegram.session"))
    assert "another" in message.lower()
    assert "Traceback" not in message


def test_unsafe_path_is_reported_without_a_traceback():
    message = describe(UnsafePath("/etc/passwd", "/state/downloads"))
    assert "Traceback" not in message
    assert "/state/downloads" in message


def test_login_hint_is_one_actionable_line():
    assert "telegram-login" in LOGIN_HINT
    assert "\n" not in LOGIN_HINT.strip()


def test_unexpected_errors_are_summarised_not_dumped():
    message = describe(RuntimeError("boom"))
    assert "boom" in message
    assert "Traceback" not in message


def test_describe_never_raises():
    class Awkward(Exception):
        def __str__(self):
            raise ValueError("unprintable")

    assert isinstance(describe(Awkward()), str)


def test_plugin_errors_share_a_base():
    from telegram_plugin.errors import TelegramPluginError

    for error in (NotAuthorized(), SessionLocked("/x"), UnsafePath("/a", "/b")):
        assert isinstance(error, TelegramPluginError)



def test_unknown_chat_ref_reaches_the_model_without_a_class_prefix():
    from telegram_plugin.refs import UnknownChatRef

    message = describe(UnknownChatRef("nonsense"))
    assert not message.startswith("UnknownChatRef")
    assert "t.me" in message
