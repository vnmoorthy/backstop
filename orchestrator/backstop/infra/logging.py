"""Structured JSON logging + PHI-scrubbing filter.

Emits one JSON object per log record and runs every record through a
PHI-scrubbing filter before it reaches a handler. Adapters log ids, token
counts, latency, ``finish_reason`` and ``mode`` — never message bodies, raw
keys, or PHI (closes audit finding #21). The scrub filter is defence-in-depth:
the type system already keeps PHI off egress ports, but a stray ``%s`` of a raw
string is masked here before it can be written to a log sink.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any, Dict, Pattern, Tuple

if TYPE_CHECKING:  # pragma: no cover - typing only
    from backstop.infra.config import Settings

__all__ = ["PhiScrubFilter", "JsonFormatter", "configure_logging"]

# Coarse PHI-shaped patterns masked in any log message as defence-in-depth.
# Each entry is (compiled-pattern, replacement-token). Ordering matters: more
# specific patterns (SSN, NPI) run before the generic long-digit run.
_PHI_PATTERNS: Tuple[Tuple[Pattern[str], str], ...] = (
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN]"),
    (re.compile(r"\b\d{10}\b"), "[NPI]"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "[EMAIL]"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}\b"), "[DATE]"),
    (re.compile(r"\b\d{7,}\b"), "[ID]"),
)

# Standard ``LogRecord`` attributes that are not part of the user-supplied
# "extra" dict; everything else on the record is treated as structured context.
_RESERVED_RECORD_ATTRS = frozenset(
    {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "taskName", "asctime", "message",
    }
)


def scrub_phi(text: str) -> str:
    """Mask PHI-shaped substrings in ``text`` with category tokens."""
    scrubbed = text
    for pattern, token in _PHI_PATTERNS:
        scrubbed = pattern.sub(token, scrubbed)
    return scrubbed


class PhiScrubFilter(logging.Filter):
    """A logging filter that redacts PHI-shaped tokens from records."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Scrub PHI from ``record`` in place and allow it through.

        Renders the message (interpolating any args) and scrubs the result, so
        a PHI value passed via ``%s`` is masked too. Returns ``True`` always —
        the record is kept, scrubbed.
        """
        try:
            rendered = record.getMessage()
        except (TypeError, ValueError):
            rendered = str(record.msg)
        record.msg = scrub_phi(rendered)
        record.args = None
        return True


class JsonFormatter(logging.Formatter):
    """Format a :class:`logging.LogRecord` as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        """Render ``record`` as a compact JSON string.

        Emits a stable envelope (``ts``, ``level``, ``logger``, ``msg``) plus any
        structured ``extra`` fields the caller attached, each value coerced to a
        JSON-safe form. The message is already scrubbed by :class:`PhiScrubFilter`
        when that filter is installed.
        """
        payload: Dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED_RECORD_ATTRS or key.startswith("_"):
                continue
            payload[key] = self._json_safe(value)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, separators=(",", ":"), default=str)

    @staticmethod
    def _json_safe(value: Any) -> Any:
        """Coerce ``value`` to something :func:`json.dumps` accepts directly."""
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)


def configure_logging(settings: Settings) -> None:
    """Install the JSON formatter + PHI-scrub filter on the root logger.

    Idempotent: replaces any handlers a previous call (or uvicorn) installed on
    the root logger with a single stream handler carrying the JSON formatter and
    the PHI-scrub filter.

    Args:
        settings: The frozen application settings.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(PhiScrubFilter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
