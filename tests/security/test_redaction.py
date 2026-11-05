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
