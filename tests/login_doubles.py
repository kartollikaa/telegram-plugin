"""Stand-ins for the Telethon calls the login makes and for the operator at the terminal.

Shared by the in-process tests and by the child process the pty tests drive."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from telethon.errors import (
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
)
from telethon.tl.types import User
from telethon.tl.types.auth import SentCodeTypeApp


class StubQr:
    """Behaves like telethon's QRLogin against the real server: `url` and `expires`
    describe the current token, `wait` resolves only while it is running, and `recreate`
    gets a new token only once the old one has expired by the server's clock. Telegram
    hands back the same token before that; a double that always issued a fresh one hid
    a rotation schedule that redrew a dead code."""

    def __init__(self, lifetime=30.0, scan_after=None, outcome="ok", consumed_on_recreate=None,
                 server_offset=0.0):
        self.lifetime = lifetime
        self.server_offset = server_offset
        """Seconds the server's clock is ahead of this one; negative when ours runs ahead."""
        self.scan_after = scan_after
        self.outcome = outcome
        self.consumed_on_recreate = consumed_on_recreate
        self.tokens = 0
        self.recreations: list[float] = []
        """Seconds the replaced token still had when each recreate was asked for."""
        self.wait_calls = 0
        self.wait_cancelled = False
        self._consumed = False
        self._fresh()

    def _server_now(self):
        return datetime.now(timezone.utc) + timedelta(seconds=self.server_offset)

    def _fresh(self):
        self.tokens += 1
        self._url = f"tg://login?token=TOKEN{self.tokens}"
        self._expires = self._server_now() + timedelta(seconds=self.lifetime)

    @property
    def url(self):
        if self._consumed:
            raise AttributeError("token")
        return self._url

    @property
    def expires(self):
        if self._consumed:
            raise AttributeError("expires")
        return self._expires

    async def recreate(self):
        self.recreations.append((self._expires - datetime.now(timezone.utc)).total_seconds())
        if self.consumed_on_recreate == len(self.recreations):
            self._consumed = True
            return
        if self._server_now() >= self._expires:
            self._fresh()

    async def wait(self, timeout=None):
        self.wait_calls += 1
        try:
            if self.scan_after is None:
                await asyncio.sleep(timeout)
                raise asyncio.TimeoutError
            await asyncio.sleep(self.scan_after)
        except asyncio.CancelledError:
            self.wait_cancelled = True
            raise
        if self.outcome == "password":
            raise SessionPasswordNeededError(request=None)
        return SimpleNamespace(id=42)


class StubClient:
    def __init__(
        self,
        qr=None,
        *,
        authorized=False,
        codes=("ok",),
        passwords=("ok",),
        invalid_phones=(),
        hint=None,
        connect_error=None,
        flood_on_send=None,
        time_offset=None,
    ):
        self.qr = qr or StubQr()
        self._authorized = authorized
        self.codes = list(codes)
        self.passwords = list(passwords)
        self.invalid_phones = set(invalid_phones)
        self.hint = hint
        self.connect_error = connect_error
        self.flood_on_send = flood_on_send
        """The send_code_request call, counted from 1, that Telegram answers with a flood wait."""
        if time_offset is not None:
            self._sender = SimpleNamespace(_state=SimpleNamespace(time_offset=time_offset))
        self.sent: list[str] = []
        self.signed_in_with: list[str] = []
        self.disconnected = False
        self.logout_calls = 0

    async def connect(self):
        if self.connect_error:
            raise self.connect_error
        return True

    async def is_user_authorized(self):
        return self._authorized

    async def qr_login(self):
        return self.qr

    async def send_code_request(self, phone):
        self.sent.append(phone)
        if self.flood_on_send == len(self.sent):
            raise FloodWaitError(request=None, capture=42)
        if phone in self.invalid_phones:
            raise PhoneNumberInvalidError(request=None)
        return SimpleNamespace(type=SentCodeTypeApp(length=5))

    async def sign_in(self, phone=None, code=None, password=None):
        if password is None:
            self.signed_in_with.append("code")
            outcome = self.codes.pop(0)
            if outcome == "wrong":
                raise PhoneCodeInvalidError(request=None)
            if outcome == "expired":
                raise PhoneCodeExpiredError(request=None)
            if outcome == "password":
                raise SessionPasswordNeededError(request=None)
        else:
            self.signed_in_with.append("password")
            if self.passwords.pop(0) == "wrong":
                raise PasswordHashInvalidError(request=None)
        return SimpleNamespace(id=42)

    async def __call__(self, request):
        return SimpleNamespace(hint=self.hint)

    async def get_me(self):
        return User(id=42, first_name="Test", username="tester")

    async def disconnect(self):
        self.disconnected = True

    async def log_out(self):
        self.logout_calls += 1
        return True


class ScriptedUi:
    """The operator: answers prompts from a script and presses keys on a timetable.

    An answer that is an exception instance is raised instead, the way Ctrl-C or
    Ctrl-D surfaces from a real prompt."""

    def __init__(self, answers=(), secrets=(), keys=(), *, method="2"):
        self.answers = ([method] if method is not None else []) + list(answers)
        self.secrets = list(secrets)
        self.keys = sorted(keys)
        self.events: list[tuple] = []
        self._entered: float | None = None

    def say(self, text=""):
        self.events.append(("say", text))

    def ask(self, prompt):
        self.events.append(("ask", prompt))
        return self._next(self.answers)

    def ask_secret(self, prompt):
        self.events.append(("secret", prompt))
        return self._next(self.secrets)

    @staticmethod
    def _next(queue):
        value = queue.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    def enter_qr(self):
        self._entered = time.monotonic()
        self.events.append(("enter_qr",))

    def show_qr(self, url, seconds):
        self.events.append(("qr", url, seconds))

    def countdown(self, seconds):
        self.events.append(("countdown", seconds))

    def notice(self, text):
        self.events.append(("notice", text))

    def poll_key(self):
        if self.keys and time.monotonic() - self._entered >= self.keys[0][0]:
            return self.keys.pop(0)[1]
        return None

    def leave_qr(self):
        self.events.append(("leave_qr",))

    def said(self):
        return [event[1] for event in self.events if event[0] == "say"]

    def shown(self):
        return [event[1] for event in self.events if event[0] == "qr"]
