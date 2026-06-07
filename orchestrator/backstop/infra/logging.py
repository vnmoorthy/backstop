"""Structured JSON logging + PHI-scrubbing filter (stub).

Emits one JSON object per log record and runs every record through a
PHI-scrubbing filter before it reaches a handler. Adapters log ids, token
counts, latency, ``finish_reason`` and ``mode`` — never message bodies, raw
keys, or PHI (closes audit finding #21).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from backstop.infra.config import Settings


class PhiScrubFilter(logging.Filter):
    """A logging filter that redacts PHI-shaped tokens from records."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Scrub PHI from ``record`` in place and allow it through.

        Args:
            record: The log record about to be emitted.

        Returns:
            ``True`` always (the record is kept, scrubbed).

        Raises:
            NotImplementedError: Always — filled in by WS-Integration.
        """
        raise NotImplementedError("PhiScrubFilter.filter is implemented in WS-Integration")


class JsonFormatter(logging.Formatter):
    """Format a :class:`logging.LogRecord` as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        """Render ``record`` as a compact JSON string.

        Args:
            record: The log record to serialise.

        Returns:
            A JSON-encoded line.

        Raises:
            NotImplementedError: Always — filled in by WS-Integration.
        """
        raise NotImplementedError("JsonFormatter.format is implemented in WS-Integration")


def configure_logging(settings: Settings) -> None:
    """Install the JSON formatter + PHI-scrub filter on the root logger.

    Args:
        settings: The frozen application settings.

    Raises:
        NotImplementedError: Always — filled in by WS-Integration.
    """
    raise NotImplementedError("configure_logging is implemented in WS-Integration")
