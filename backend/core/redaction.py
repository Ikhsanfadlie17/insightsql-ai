"""Defence in depth for secrets: remove configured secrets from log records and from
error text that reaches clients or the database.

Our own error messages never contain the API key (provider bodies are never copied), but a
third-party exception or a future change could; this layer makes that harmless.
"""

from __future__ import annotations

import logging
import threading

REDACTED = "[REDACTED]"
_MIN_SECRET_LENGTH = 8  # never redact short strings: they would mangle normal text

_secrets: set[str] = set()
_lock = threading.Lock()
_installed = False


def register_secret(secret: str | None) -> None:
    """Redact ``secret`` from now on, in this process."""
    if secret and len(secret) >= _MIN_SECRET_LENGTH:
        with _lock:
            _secrets.add(secret)
        _install_log_redaction()


def redact(text: str) -> str:
    """``text`` with every registered secret replaced."""
    for secret in _secrets:
        if secret in text:
            text = text.replace(secret, REDACTED)
    return text


def _contains_secret(text: str) -> bool:
    return any(secret in text for secret in _secrets)


def _install_log_redaction() -> None:
    """Wrap the log record factory once, so every handler (including test capture
    handlers added later) receives redacted messages and tracebacks."""
    global _installed
    with _lock:
        if _installed:
            return
        base = logging.getLogRecordFactory()

        def factory(*args: object, **kwargs: object) -> logging.LogRecord:
            record = base(*args, **kwargs)
            if not _secrets:
                return record
            try:
                message = record.getMessage()
            except Exception:  # a broken format string is logging's problem, not ours
                return record
            if _contains_secret(message):
                record.msg, record.args = redact(message), None
            if record.exc_info and record.exc_info[1] is not None and not record.exc_text:
                text = logging.Formatter().formatException(record.exc_info)
                if _contains_secret(text):
                    record.exc_text = redact(text)  # formatters use exc_text when set
            return record

        logging.setLogRecordFactory(factory)
        _installed = True
