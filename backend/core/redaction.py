"""Defence in depth for secrets: remove configured secrets from log records and from
error text that reaches clients or the database.

Our own error messages never contain the API key (provider bodies are never copied), but a
third-party exception or a future change could; this layer makes that harmless.
"""

from __future__ import annotations

import contextlib
import logging
import threading

REDACTED = "[REDACTED]"
_MIN_SECRET_LENGTH = 8  # never redact short strings: they would mangle normal text

# Copy-on-write: readers iterate an immutable snapshot, so registering a secret while
# another thread logs can never fail with "set changed size" (Phase 7 review L1).
_secrets: frozenset[str] = frozenset()
_lock = threading.Lock()
_installed = False


def register_secret(secret: str | None) -> None:
    """Redact ``secret`` from now on, in this process."""
    global _secrets
    if secret and len(secret) >= _MIN_SECRET_LENGTH:
        with _lock:
            _secrets = _secrets | {secret}
        _install_log_redaction()


def redact(text: str) -> str:
    """``text`` with every registered secret replaced."""
    for secret in _secrets:
        if secret in text:
            text = text.replace(secret, REDACTED)
    return text


def _contains_secret(text: str) -> bool:
    return any(secret in text for secret in _secrets)


def _redact_record(record: logging.LogRecord) -> None:
    message = record.getMessage()
    if _contains_secret(message):
        record.msg, record.args = redact(message), None
    if record.exc_info and record.exc_info[1] is not None and not record.exc_text:
        text = logging.Formatter().formatException(record.exc_info)
        if _contains_secret(text):
            record.exc_text = redact(text)  # standard formatters use exc_text when set
    if record.stack_info and _contains_secret(record.stack_info):
        record.stack_info = redact(record.stack_info)


def _install_log_redaction() -> None:
    """Wrap the log record factory once, so every handler (including capture handlers added
    later, as in tests) receives redacted messages, tracebacks, and stack info.

    Limits: a library that replaces the record factory afterwards, or a custom formatter
    that formats exceptions itself, bypasses this layer.
    """
    global _installed
    with _lock:
        if _installed:
            return
        base = logging.getLogRecordFactory()

        def factory(*args: object, **kwargs: object) -> logging.LogRecord:
            record = base(*args, **kwargs)
            if _secrets:
                # Redaction must never break the caller's logging call.
                with contextlib.suppress(Exception):
                    _redact_record(record)
            return record

        logging.setLogRecordFactory(factory)
        _installed = True
