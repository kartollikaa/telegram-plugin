"""Redaction at the plugin's output boundary, not a sandbox for local code."""

from __future__ import annotations

import json
import logging
import re
import traceback
from contextvars import ContextVar
from functools import wraps
from typing import Any


class Redactor:
    def __init__(self) -> None:
        self._id_patterns: list[re.Pattern] = []
        self._hash_patterns: list[tuple[int, re.Pattern]] = []
        self._api_ids: set[int] = set()

    def register(self, api_id: int | None, api_hash: str | None) -> None:
        if api_id:
            self._api_ids.add(api_id)
            self._id_patterns.append(re.compile(r"(?<![0-9])" + re.escape(str(api_id)) + r"(?![0-9])"))
        if api_hash:
            pattern = re.escape(api_hash)
            if len(api_hash) < 8:
                pattern = r"(?<![\w])" + pattern + r"(?![\w])"
            self._hash_patterns.append((len(api_hash), re.compile(pattern, re.IGNORECASE)))
            self._hash_patterns.sort(key=lambda item: item[0], reverse=True)

    def text(self, value: str) -> str:
        for _, pattern in self._hash_patterns:
            value = pattern.sub("[redacted]", value)
        for pattern in self._id_patterns:
            value = pattern.sub("[redacted]", value)
        return value

    def value(self, value: Any) -> Any:
        if type(value) is int and value in self._api_ids:
            return "[redacted]"
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {self.text(str(key)): "[redacted]" if str(key).lower() in {
                "api_id", "api_hash", "telegram_api_id", "telegram_api_hash",
            } else self.value(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.value(item) for item in value]
        return value


_active: ContextVar[Redactor | None] = ContextVar("output_redactor", default=None)


def register_credentials(api_id: int | None, api_hash: str | None) -> None:
    if (redactor := _active.get()) is not None:
        redactor.register(api_id, api_hash)


def redact_text(value: str) -> str:
    redactor = _active.get()
    return redactor.text(value) if redactor else value


def redact_value(value: Any, *, credentials: tuple | None = None) -> Any:
    redactor = _active.get() or Redactor()
    if credentials:
        redactor.register(*credentials)
    return redactor.value(value)


def private_json(value: Any, **options) -> str:
    return json.dumps(redact_value(value), **options)


def private_output(operation):
    @wraps(operation)
    def run(*args, **kwargs):
        redactor = Redactor()
        token = _active.set(redactor)
        previous_factory = logging.getLogRecordFactory()

        def factory(*record_args, **record_kwargs):
            record = previous_factory(*record_args, **record_kwargs)
            try:
                record.msg = redactor.text(record.getMessage())
            except Exception:  # noqa: BLE001
                record.msg = "unformattable diagnostic"
            record.args = ()
            if record.exc_info:
                record.exc_text = redactor.text("".join(traceback.format_exception(*record.exc_info)))
                record.exc_info = None
            if record.stack_info:
                record.stack_info = redactor.text(record.stack_info)
            return record

        logging.setLogRecordFactory(factory)
        try:
            return operation(*args, **kwargs)
        finally:
            logging.setLogRecordFactory(previous_factory)
            _active.reset(token)

    return run
