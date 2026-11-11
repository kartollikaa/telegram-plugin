import asyncio
import contextlib
import json
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from telegram_plugin import login
from telegram_plugin.config import load_config
from telegram_plugin.login import interactive_login, interactive_logout
from tests.login_doubles import StubClient, StubQr


@pytest.fixture
def page():
    from telegram_plugin.browser_login import BrowserUi

    urls = []
    ui = BrowserUi(opener=lambda url: urls.append(url) or True, timeout=2)
    yield ui, urls[0]
    ui.close()


def request(url, *, body=None, origin=None, host=None):
    headers = {}
    if body is not None:
        headers = {"Content-Type": "application/json", "Origin": origin or url.split('/s/')[0]}
    if host:
        headers["Host"] = host
    data = None if body is None else json.dumps(body).encode()
    try:
        with urlopen(Request(url, data=data, headers=headers), timeout=3) as response:
            return response.read(), response.headers
    except HTTPError as error:
        error.close()
        raise


def wait_prompt(url, text):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        data = json.loads(request(url + "state")[0])
        if data.get("prompt") and text in data["prompt"]["text"]:
            return data
        time.sleep(0.01)
    pytest.fail(f"prompt not shown: {text}")


def answer(url, text, value):
    data = wait_prompt(url, text)
    request(url + "answer", body={"csrf": data["csrf"],
                                  "revision": data["prompt"]["revision"], "answer": value})


def start(task):
    results = []

    def run():
        results.append(asyncio.run(task))

    thread = threading.Thread(target=run)
    thread.start()
    return thread, results


def config(tmp_path):
    return load_config({"TELEGRAM_STATE_DIR": str(tmp_path),
                        "TELEGRAM_API_ID": "1", "TELEGRAM_API_HASH": "h"})


def test_phone_code_and_two_factor_complete_in_automatically_opened_form(page, tmp_path):
    ui, url = page
    client = StubClient(codes=["password"])
    thread, results = start(interactive_login(config(tmp_path), client, ui, method="phone"))
    answer(url, "Phone number", "+447700900123")
    data = wait_prompt(url, "Login code")
    assert data["prompt"]["secret"]
    answer(url, "Login code", "24680")
    assert wait_prompt(url, "Password")["prompt"]["secret"]
    answer(url, "Password", "dummy-two-factor")
    thread.join(3)
    assert not thread.is_alive()
    assert results[0]["state"] == "authorized"
    assert client.signed_in_with == ["code", "password"]
    serialized = request(url + "state")[0].decode()
    assert "24680" not in serialized and "dummy-two-factor" not in serialized


def test_qr_approval_continues_to_two_factor_in_same_form(page, tmp_path):
    ui, url = page
    client = StubClient(StubQr(scan_after=0.03, outcome="password"))
    thread, results = start(interactive_login(config(tmp_path), client, ui, method="qr"))
    answer(url, "Password", "dummy-two-factor")
    thread.join(3)
    assert results[0]["state"] == "authorized"
    assert client.qr.wait_calls == 1


@pytest.mark.parametrize("value,expected,calls", [("yes", "logged_out", 1),
                                               ("no", "cancelled", 0)])
def test_logout_confirmation_stays_in_local_form(page, tmp_path, value, expected, calls):
    ui, url = page
    client = StubClient(authorized=True)
    thread, results = start(interactive_logout(config(tmp_path), client, ui))
    answer(url, "Log out Test", value)
    thread.join(3)
    assert results[0]["state"] == expected
    assert client.logout_calls == calls


def test_wrong_origin_host_and_csrf_are_rejected_with_valid_control(page):
    _, url = page
    state = json.loads(request(url + "state")[0])
    body = {"csrf": state["csrf"]}
    for extra in [{"origin": "https://foreign.invalid"},
                  {"host": "foreign.invalid"}, {"body": {"csrf": "wrong"}}]:
        with pytest.raises(HTTPError) as error:
            request(url + "cancel", **({"body": body} | extra))
        assert error.value.code == 403
    assert request(url + "cancel", body=body)[0] == b'{"ok": true}'


def test_answers_cannot_be_replayed_into_the_next_prompt(page):
    ui, url = page
    answers = []
    thread = threading.Thread(target=lambda: answers.extend([
        ui.ask_secret("First"), ui.ask_secret("Second")]))
    thread.start()
    data = wait_prompt(url, "First")
    body = {"csrf": data["csrf"], "revision": data["prompt"]["revision"], "answer": "one"}
    request(url + "answer", body=body)
    wait_prompt(url, "Second")
    with pytest.raises(HTTPError) as error:
        request(url + "answer", body=body)
    assert error.value.code == 409
    answer(url, "Second", "two")
    thread.join(3)
    assert answers == ["one", "two"]


def test_page_is_private_uncached_and_secret_fields_clear_after_submit(page):
    _, url = page
    html, headers = request(url)
    assert headers["Cache-Control"] == "no-store"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert b"input.value = ''" in html
    assert b"'password'" in html
    with pytest.raises(HTTPError) as error:
        request(url.replace('/s/', '/wrong/') + "state")
    assert error.value.code == 404


def test_failed_browser_launch_is_not_reported_as_opened():
    from telegram_plugin.browser_login import BrowserUi

    with pytest.raises(RuntimeError, match="could not open"):
        BrowserUi(opener=lambda _: False)


@pytest.mark.parametrize("mode", ["phone", "resume", "logout"])
def test_cli_browser_mode_works_without_tty_and_always_closes(tmp_path, monkeypatch, mode):
    from telegram_plugin import browser_login, terminal
    from tests.login_doubles import ScriptedUi

    class Form(ScriptedUi):
        async def run(self, operation):
            return await operation

        def finish(self, payload):
            self.result = payload

        def close(self):
            self.closed = True

    form = Form(method=None, answers=["yes"] if mode == "logout" else ["+447700900123"],
                secrets=["24680", "dummy-two-factor"])
    client = StubClient(authorized=mode == "logout")
    monkeypatch.setenv("TELEGRAM_API_ID", "1")
    monkeypatch.setenv("TELEGRAM_API_HASH", "h")
    monkeypatch.setattr(terminal, "is_interactive", lambda *_: False)
    monkeypatch.setattr(browser_login, "BrowserUi", lambda: form)
    if mode == "logout":
        (tmp_path / "telegram.session").touch()
    flags = ["--method", mode] if mode == "phone" else ["--" + mode]
    assert login.main(["--state-dir", str(tmp_path), "--browser", *flags],
                      client_factory=lambda _: client) == 0
    assert form.closed and form.result["state"] in {"authorized", "logged_out"}
    assert client.disconnected


def test_form_cancel_and_timeout_release_hidden_prompt(page):
    ui, url = page
    outcomes = []

    def run():
        try:
            ui.ask_secret("Code")
        except EOFError:
            outcomes.append("cancelled")

    thread = threading.Thread(target=run)
    thread.start()
    state = wait_prompt(url, "Code")
    request(url + "cancel", body={"csrf": state["csrf"]})
    thread.join(3)
    assert outcomes == ["cancelled"]
    ui._cancelled = False
    ui._deadline = time.monotonic() - 1
    with pytest.raises(EOFError):
        ui.ask_secret("Expired")


@pytest.mark.parametrize("scenario", ["menu_phone", "phone_retries", "qr", "qr_password",
                                      "resume", "logout_yes", "logout_no", "network_error"])
def test_cli_complete_workflow_through_real_loopback_form(
    tmp_path, monkeypatch, capsys, scenario,
):
    from telegram_plugin import browser_login, terminal

    browser_type = browser_login.BrowserUi
    code = "24680"
    password = "dummy-two-factor"
    cases = {
        "menu_phone": ([], [("method", "wrong"), ("method", "1"),
                            ("Phone number", "+447700900123"), ("Login code", code)], 0),
        "phone_retries": (["--method", "phone"], [("Phone number", "+447700900123"),
                           ("Login code", code), ("Login code", code), ("Login code", code),
                           ("Password", password), ("Password", password)], 0),
        "qr": (["--method", "qr"], [], 0),
        "qr_password": (["--method", "qr"], [("Password", password)], 0),
        "resume": (["--resume"], [("Password", password)], 0),
        "logout_yes": (["--logout"], [("Log out", "yes")], 0),
        "logout_no": (["--logout"], [("Log out", "no")], 1),
        "network_error": (["--method", "phone"], [], 1),
    }
    flags, steps, expected_exit = cases[scenario]
    forms, threads, errors, results, urls = [], [], [], [], []

    def open_form(url):
        urls.append(url)

        def drive():
            try:
                for prompt, value in steps:
                    state = wait_prompt(url, prompt)
                    request(url + "answer", body={"csrf": state["csrf"],
                            "revision": state["prompt"]["revision"], "answer": value})
                    deadline = time.monotonic() + 2
                    while time.monotonic() < deadline:
                        current = json.loads(request(url + "state")[0])
                        if current["result"]:
                            results.append(current)
                            return
                        if current["prompt"] != state["prompt"]:
                            break
                        time.sleep(0.01)
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    state = json.loads(request(url + "state")[0])
                    if state["result"]:
                        results.append(state)
                        return
                    time.sleep(0.01)
                errors.append("no final result")
            except (OSError, ValueError) as error:
                errors.append(type(error).__name__)

        thread = threading.Thread(target=drive)
        threads.append(thread)
        thread.start()
        return True

    def create_form():
        form = browser_type(opener=open_form, timeout=3)
        forms.append(form)
        return form

    client = StubClient(StubQr(scan_after=0.03, outcome=(
        "password" if scenario == "qr_password" else "ok")),
        authorized=scenario.startswith("logout"),
        codes=["wrong", "expired", "password"] if scenario == "phone_retries" else ["ok"],
        passwords=["wrong", "ok"] if scenario == "phone_retries" else ["ok"],
        connect_error=ConnectionError("offline") if scenario == "network_error" else None)
    if scenario.startswith("logout"):
        (tmp_path / "telegram.session").touch()
    monkeypatch.setenv("TELEGRAM_API_ID", "1")
    monkeypatch.setenv("TELEGRAM_API_HASH", "h")
    monkeypatch.setattr(terminal, "is_interactive", lambda *_: False)
    monkeypatch.setattr(browser_login, "BrowserUi", create_form)
    assert login.main(["--state-dir", str(tmp_path), "--browser", *flags],
                      client_factory=lambda _: client) == expected_exit
    for thread in threads:
        thread.join(3)
        assert not thread.is_alive()
    assert not errors and results
    assert client.disconnected and forms[0]._closed
    assert not forms[0]._thread.is_alive()
    assert forms[0]._answer is None and forms[0]._prompt is None
    assert code not in json.dumps(results) and password not in json.dumps(results)
    output = capsys.readouterr()
    for value in (code, password):
        assert value not in output.out + output.err
        assert value not in (tmp_path / "auth-status.json").read_text()
    if scenario == "phone_retries":
        assert len(client.sent) == 2
        assert client.signed_in_with == ["code", "code", "code", "password", "password"]


def test_browser_result_belongs_to_this_invocation_after_session_lock_is_released(
    tmp_path, monkeypatch,
):
    from telegram_plugin import browser_login
    from tests.login_doubles import ScriptedUi

    results = []

    class Form(ScriptedUi):
        async def run(self, operation):
            return await operation

        def finish(self, payload):
            results.append(payload)

        def close(self):
            pass

    lock = login.session_lock

    @contextlib.contextmanager
    def competing_lock(*args):
        with lock(*args):
            yield
        login.write_status(config(tmp_path), {"state": "cancelled", "hint": "another invocation"})

    monkeypatch.setenv("TELEGRAM_API_ID", "1")
    monkeypatch.setenv("TELEGRAM_API_HASH", "h")
    monkeypatch.setattr(browser_login, "BrowserUi", Form)
    monkeypatch.setattr(login, "session_lock", competing_lock)
    assert login.main(["--state-dir", str(tmp_path), "--browser"],
                      client_factory=lambda _: StubClient(authorized=True)) == 0
    assert results[0]["state"] == "authorized"
    assert json.loads((tmp_path / "auth-status.json").read_text())["state"] == "cancelled"


@pytest.mark.parametrize("stop", ["cancel", "deadline"])
async def test_form_stop_cancels_pending_network_call_and_disconnects(page, tmp_path, stop):
    ui, url = page
    waiting = asyncio.Event()

    class HangingClient(StubClient):
        async def connect(self):
            waiting.set()
            await asyncio.Event().wait()

    client = HangingClient()
    task = asyncio.create_task(ui.run(interactive_login(config(tmp_path), client, ui,
                                                       method="phone")))
    await waiting.wait()
    if stop == "cancel":
        state = json.loads(request(url + "state")[0])
        request(url + "cancel", body={"csrf": state["csrf"]})
    else:
        ui._deadline = time.monotonic() - 1
    with pytest.raises(EOFError):
        await asyncio.wait_for(task, timeout=0.5)
    assert client.disconnected


def test_final_result_is_acknowledged_only_after_response_is_sent(page, monkeypatch):
    ui, url = page
    ui._result = "Finished"
    handler = ui._server.RequestHandlerClass
    send = handler._send
    seen_before_send = []

    def capture(self, value, *args, **kwargs):
        if isinstance(value, dict) and value.get("result"):
            seen_before_send.append(ui._seen.is_set())
        return send(self, value, *args, **kwargs)

    monkeypatch.setattr(handler, "_send", capture)
    assert json.loads(request(url + "state")[0])["result"] == "Finished"
    assert seen_before_send == [False]
    assert ui._seen.wait(1)
