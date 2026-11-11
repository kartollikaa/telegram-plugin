"""Human-only authentication prompts on a short-lived loopback page."""

from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from telegram_plugin.qr import qr_matrix

PAGE = """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Telegram sign in</title><style nonce="NONCE">
body{font:17px system-ui;max-width:440px;margin:8vh auto;padding:24px;background:#f6f7fb;color:#171923}
main{background:white;padding:28px;border-radius:16px}h1{font-size:24px}
input,select,button{box-sizing:border-box;width:100%;font:inherit;padding:12px;margin:8px 0}
button{cursor:pointer}pre{white-space:pre-wrap;font:inherit}canvas{width:100%;image-rendering:pixelated}
[hidden]{display:none!important}.note{font-size:14px;color:#555}
</style><main><h1>Telegram</h1><p class="note">Enter your code and password here.
The local plugin sends them to Telegram, never to the agent.</p><pre id="messages"></pre>
<canvas id="qr" hidden></canvas><form id="form" hidden autocomplete="off">
<label id="label" for="answer"></label><input id="answer" autocomplete="off">
<button>Continue</button></form><p id="result" role="status">Connecting…</p>
<button id="cancel" type="button">Cancel</button></main><script nonce="NONCE">
const form = document.getElementById('form'), input = document.getElementById('answer');
const result = document.getElementById('result'), canvas = document.getElementById('qr');
let current = null, csrf = '', finished = false, busy = false;
async function post(action, body) {
  const response = await fetch(action, {method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({...body, csrf}), cache:'no-store'});
  if (!response.ok) throw new Error('This prompt changed. Please try again.');
}
form.addEventListener('submit', async event => {
  event.preventDefault(); if (busy || !current) return;
  const answer = input.value, revision = current.revision;
  input.value = ''; busy = true; form.querySelector('button').disabled = true;
  try { await post('answer', {answer, revision}); form.hidden = true; current = null; }
  catch (error) { result.textContent = error.message; }
  finally { busy = false; form.querySelector('button').disabled = false; }
});
document.getElementById('cancel').addEventListener('click', async () => {
  input.value = '';
  try { await post('cancel', {}); }
  catch (_) { result.textContent = 'Connection closed. Return to the agent for its result.'; }
});
async function poll() {
  if (finished) return;
  try {
    const response = await fetch('state', {cache:'no-store'});
    const state = await response.json(); csrf = state.csrf;
    document.getElementById('messages').textContent = state.messages.join('\\n');
    if (state.result) {
      result.textContent = state.result; finished = true; form.hidden = true;
      canvas.hidden = true; input.value = ''; document.getElementById('cancel').hidden = true;
      return;
    }
    result.textContent = state.prompt ? '' : 'Waiting for Telegram…';
    if (state.prompt && (!current || current.revision !== state.prompt.revision)) {
      current = state.prompt; input.value = ''; input.type = current.secret ? 'password' : 'text';
      document.getElementById('label').textContent = current.text; form.hidden = false;
      input.focus();
    }
    if (!state.prompt) { form.hidden = true; current = null; }
    canvas.hidden = !state.matrix;
    if (state.matrix) {
      const size = state.matrix.length; canvas.width = size * 8; canvas.height = size * 8;
      const ctx = canvas.getContext('2d'); ctx.fillStyle = '#fff';
      ctx.fillRect(0, 0, canvas.width, canvas.height); ctx.fillStyle = '#000';
      state.matrix.forEach((row, y) => row.forEach((dark, x) => {
        if (dark) ctx.fillRect(x * 8, y * 8, 8, 8);
      }));
    }
  } catch (_) { result.textContent = 'Sign-in connection closed. Return to the agent for its result.'; }
  if (!finished) setTimeout(poll, 250);
}
poll();
</script></html>"""


class BrowserUi:
    def __init__(self, *, opener=None, timeout: float = 300) -> None:
        self._condition = threading.Condition()
        self._messages: list[str] = []
        self._prompt: dict | None = None
        self._answer: str | None = None
        self._matrix: list | None = None
        self._revision = 0
        self._cancelled = False
        self._closed = False
        self._result: str | None = None
        self._seen = threading.Event()
        self._timeout = timeout
        self._deadline = time.monotonic() + timeout
        self._csrf = secrets.token_urlsafe(32)
        self._prefix = f"/s/{secrets.token_urlsafe(32)}/"
        self._nonce = secrets.token_urlsafe(24)
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._server.daemon_threads = True
        self._origin = f"http://127.0.0.1:{self._server.server_port}"
        self._thread = threading.Thread(
            target=lambda: self._server.serve_forever(poll_interval=0.05), daemon=True,
        )
        self._thread.start()
        try:
            if not (opener or webbrowser.open)(self._origin + self._prefix):
                raise RuntimeError("could not open the local sign-in form on this device")
        except Exception:  # noqa: BLE001
            self.close()
            raise RuntimeError("could not open the local sign-in form on this device") from None

    def _handler(self):
        ui = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(5)

            def log_message(self, *_):
                pass

            def _send(self, value: Any, code: int = 200, *, html: bool = False):
                body = value.encode() if html else json.dumps(value).encode()
                self.send_response(code)
                self.send_header("Content-Type", "text/html; charset=utf-8" if html
                                 else "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "default-src 'none'; "
                                 f"script-src 'nonce-{ui._nonce}'; style-src 'nonce-{ui._nonce}'; "
                                 "connect-src 'self'; frame-ancestors 'none'; form-action 'none'")
                self.end_headers()
                self.wfile.write(body)

            def _route(self):
                if self.headers.get("Host") != ui._origin.removeprefix("http://"):
                    self._send({}, 403)
                    return None
                if not self.path.startswith(ui._prefix):
                    self._send({}, 404)
                    return None
                return self.path[len(ui._prefix):]

            def do_GET(self):
                route = self._route()
                if route is None:
                    return
                if route == "":
                    self._send(PAGE.replace("NONCE", ui._nonce), html=True)
                elif route == "state":
                    self._send(ui._state())
                else:
                    self._send({}, 404)

            def do_POST(self):
                route = self._route()
                if route is None:
                    return
                if self.headers.get("Origin") != ui._origin:
                    self._send({}, 403)
                    return
                if (self.headers.get("Content-Type") != "application/json"
                        or self.headers.get("Transfer-Encoding")):
                    self._send({}, 400)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 8192:
                        self._send({}, 413)
                        return
                    data = json.loads(self.rfile.read(length))
                    csrf = data.get("csrf") if isinstance(data, dict) else None
                    if not isinstance(csrf, str) or not secrets.compare_digest(csrf, ui._csrf):
                        self._send({}, 403)
                        return
                except (ValueError, OSError):
                    self._send({}, 400)
                    return
                with ui._condition:
                    if route == "cancel":
                        ui._cancelled = True
                    elif route == "answer":
                        if (ui._prompt is None or type(data.get("revision")) is not int
                                or data["revision"] != ui._prompt["revision"]):
                            self._send({}, 409)
                            return
                        value = data.get("answer")
                        if not isinstance(value, str) or len(value) > 4096:
                            self._send({}, 400)
                            return
                        ui._answer, ui._prompt = value, None
                    else:
                        self._send({}, 404)
                        return
                    ui._condition.notify_all()
                self._send({"ok": True})

        return Handler

    async def run(self, operation):
        task = asyncio.create_task(operation)
        try:
            while not task.done():
                self._check_cancelled()
                await asyncio.wait({task}, timeout=0.1)
            return task.result()
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    def _state(self) -> dict:
        with self._condition:
            if self._result is not None:
                self._seen.set()
            return {"messages": list(self._messages), "prompt": self._prompt,
                    "matrix": self._matrix, "csrf": self._csrf, "result": self._result}

    def say(self, text: str = "") -> None:
        with self._condition:
            self._messages.append(text.replace("Ctrl-C", "Cancel"))

    def ask(self, prompt: str) -> str:
        return self._ask(prompt, secret=False)

    def ask_secret(self, prompt: str) -> str:
        return self._ask(prompt, secret=True)

    def _ask(self, prompt: str, *, secret: bool) -> str:
        with self._condition:
            self._revision += 1
            self._prompt = {"text": prompt, "secret": secret, "revision": self._revision}
            while True:
                self._check_cancelled()
                if self._answer is not None:
                    break
                self._condition.wait(min(0.25, self._timeout))
            answer, self._answer = self._answer, None
            return answer

    def _check_cancelled(self) -> None:
        if self._cancelled or self._closed or time.monotonic() >= self._deadline:
            raise EOFError("sign-in form cancelled or timed out")

    def enter_qr(self) -> None:
        pass

    def show_qr(self, url: str, seconds: int) -> None:
        with self._condition:
            self._matrix = qr_matrix(url, quiet_zone=4)

    def countdown(self, seconds: int) -> None:
        pass

    def notice(self, text: str) -> None:
        self.say(text)

    def poll_key(self) -> str | None:
        self._check_cancelled()
        return None

    def leave_qr(self) -> None:
        with self._condition:
            self._matrix = None

    def finish(self, payload: dict) -> None:
        with self._condition:
            account = payload.get("account", {})
            self._result = (f"Connected as {account.get('name')} (id {account.get('id')})."
                            if payload.get("state") == "authorized" else
                            payload.get("hint") or payload.get("state", "Finished"))
            self._prompt, self._answer, self._matrix = None, None, None
        self._seen.wait(1)

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._answer, self._prompt, self._matrix = None, None, None
            self._condition.notify_all()
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(2)
