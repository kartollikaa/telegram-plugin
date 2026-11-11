import json
import threading

import pytest

from telegram_plugin import login
from telegram_plugin.config import load_config
from tests.login_doubles import ScriptedUi, StubClient, StubQr

PACE = login.Pace(limit=0.1, grace=0.01, tick=0.01)


def config_for(tmp_path):
    return load_config({"TELEGRAM_STATE_DIR": str(tmp_path), "TELEGRAM_API_ID": "1",
                        "TELEGRAM_API_HASH": "h"})


async def test_phone_choice_skips_qr_and_completes_two_step_login(tmp_path):
    client = StubClient(codes=["password"])
    ui = ScriptedUi(method=None, answers=["1", "+447700900123"], secrets=["12345", "secret"])
    result = await login.interactive_login(config_for(tmp_path), client, ui, PACE)
    assert result["state"] == "authorized"
    assert client.sent == ["447700900123"]
    assert client.signed_in_with == ["code", "password"]
    assert not ui.shown()
    assert client.qr.wait_calls == 0


async def test_qr_choice_draws_qr_after_the_menu(tmp_path):
    client = StubClient(StubQr(scan_after=0.01))
    ui = ScriptedUi(method=None, answers=["2"])
    result = await login.interactive_login(config_for(tmp_path), client, ui, PACE)
    assert result["state"] == "authorized"
    assert "Phone number" in "\n".join(ui.said())
    assert "QR code" in "\n".join(ui.said())
    assert ui.events.index(next(e for e in ui.events if e[0] == "ask")) < (
        ui.events.index(("enter_qr",))
    )
    assert ui.shown()


async def test_empty_or_invalid_choice_does_not_start_authorization(tmp_path):
    client = StubClient(StubQr(scan_after=0.01))
    ui = ScriptedUi(method=None, answers=["", "wrong", "2"])
    await login.interactive_login(config_for(tmp_path), client, ui, PACE)
    prompts = [event for event in ui.events if event[0] == "ask"]
    assert len(prompts) == 3
    assert client.qr.wait_calls == 1


class LogoutClient(StubClient):
    def __init__(self, *, accepted=True, error=None):
        super().__init__(authorized=True)
        self.logout_calls = 0
        self.accepted = accepted
        self.error = error

    async def log_out(self):
        self.logout_calls += 1
        if self.error:
            raise self.error
        return self.accepted


def run_logout(tmp_path, monkeypatch, client, answers):
    monkeypatch.setenv("TELEGRAM_API_ID", "1")
    monkeypatch.setenv("TELEGRAM_API_HASH", "h")
    (tmp_path / "telegram.session").touch()
    return login.main(["--state-dir", str(tmp_path), "--logout"],
                      client_factory=lambda _: client, ui=ScriptedUi(method=None, answers=answers))


def test_logout_confirms_account_and_revokes_session(tmp_path, monkeypatch, capsys):
    client = LogoutClient()
    assert run_logout(tmp_path, monkeypatch, client, ["yes"]) == 0
    payload = json.loads((tmp_path / "auth-status.json").read_text())
    assert payload["state"] == "logged_out"
    assert client.logout_calls == 1
    assert client.disconnected
    assert "Logged out" in capsys.readouterr().out


@pytest.mark.parametrize("answer", ["", "n", "no"])
def test_declining_logout_keeps_session(tmp_path, monkeypatch, answer):
    client = LogoutClient()
    assert run_logout(tmp_path, monkeypatch, client, [answer]) == 1
    assert client.logout_calls == 0
    assert client.disconnected
    assert (tmp_path / "telegram.session").exists()
    assert json.loads((tmp_path / "auth-status.json").read_text())["state"] == "cancelled"


@pytest.mark.parametrize("accepted,error", [(False, None), (True, ConnectionError("offline"))])
def test_failed_logout_is_not_reported_as_success(tmp_path, monkeypatch, accepted, error):
    client = LogoutClient(accepted=accepted, error=error)
    assert run_logout(tmp_path, monkeypatch, client, ["y"]) == 1
    assert client.logout_calls == 1
    assert client.disconnected
    assert (tmp_path / "telegram.session").exists()
    assert json.loads((tmp_path / "auth-status.json").read_text())["state"] == "failed"


def test_logout_without_a_session_needs_no_credentials_or_client(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_API_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_API_HASH", raising=False)

    def unexpected_client(_):
        pytest.fail("no session exists to revoke")

    assert login.main(["--state-dir", str(tmp_path), "--logout"],
                      client_factory=unexpected_client) == 0
    assert not (tmp_path / "telegram.session").exists()


@pytest.mark.parametrize("state,code", [("logged_out", 0), ("needs_password", 4)])
def test_wait_reports_the_new_outcomes(tmp_path, capsys, state, code):
    config = config_for(tmp_path)
    writer = threading.Timer(0.03, lambda: login.write_status(config, {"state": state}))
    writer.start()
    try:
        assert login._wait_command(config, 1, poll=0.01) == code
        assert json.loads(capsys.readouterr().out)["state"] == state
    finally:
        writer.join()


def test_logout_uses_telethon_revocation_and_deletes_its_sqlite_session(tmp_path, monkeypatch):
    from telethon import TelegramClient
    from telethon.tl.functions.auth import LogOutRequest

    client = TelegramClient(str(tmp_path / "telegram.session"), 1, "h")
    requests = []

    async def connect():
        pass

    async def authorized():
        return True

    async def me():
        return await StubClient().get_me()

    async def call(sender, request, *args, **kwargs):
        requests.append(request)
        return True

    async def disconnect():
        if client.session:
            client.session.close()

    monkeypatch.setattr(client, "connect", connect)
    monkeypatch.setattr(client, "is_user_authorized", authorized)
    monkeypatch.setattr(client, "get_me", me)
    monkeypatch.setattr(client, "_call", call)
    monkeypatch.setattr(client, "disconnect", disconnect)
    assert run_logout(tmp_path, monkeypatch, client, ["y"]) == 0
    assert len(requests) == 1 and isinstance(requests[0], LogOutRequest)
    assert not (tmp_path / "telegram.session").exists()
