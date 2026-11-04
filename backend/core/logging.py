"""Logging configuration."""

from __future__ import annotations

import logging


def setup_logging(level: str = "INFO") -> None:
    """Configure root logging once with a consistent format."""
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
