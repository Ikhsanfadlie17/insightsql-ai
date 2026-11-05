"""Task 24: configured secrets are removed from log messages and tracebacks."""

from __future__ import annotations

import logging

import pytest

from backend.core.redaction import REDACTED, redact, register_secret

SECRET = "sk-test-redaction-ABCDEFGHIJKLMNOP"


def test_redact_replaces_registered_secrets_only() -> None:
    register_secret(SECRET)
    register_secret("short")  # too short to redact safely: ignored

    assert redact(f"key={SECRET}!") == f"key={REDACTED}!"
    assert redact("a short note") == "a short note"


def test_log_messages_and_tracebacks_are_redacted(caplog: pytest.LogCaptureFixture) -> None:
    register_secret(SECRET)
    logger = logging.getLogger("tests.redaction")

    with caplog.at_level(logging.INFO):
        logger.info("calling with %s", SECRET)
        try:
            raise RuntimeError(f"provider echoed {SECRET}")
        except RuntimeError:
            logger.exception("request failed")

    assert SECRET not in caplog.text
    assert caplog.text.count(REDACTED) >= 2
    assert "RuntimeError" in caplog.text  # the traceback is still there, only redacted


def test_stack_info_is_redacted() -> None:
    """Review L2: stack info passes through the same record factory."""
    register_secret(SECRET)
    make = logging.getLogRecordFactory()

    record = make("tests.redaction", logging.INFO, __file__, 1, "msg", None, None,
                  sinfo=f"Stack (most recent call last):\n  key={SECRET}")  # fmt: skip

    assert SECRET not in record.stack_info and REDACTED in record.stack_info


def test_a_broken_record_never_breaks_logging() -> None:
    """Redaction failures are swallowed: a bad format string must still produce a record."""
    register_secret(SECRET)
    make = logging.getLogRecordFactory()

    record = make("tests.redaction", logging.INFO, __file__, 1, "%d items", ("x",), None)

    assert record.msg == "%d items"
