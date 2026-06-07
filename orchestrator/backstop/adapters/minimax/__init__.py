"""MiniMax :class:`~backstop.ports.reasoning_port.ReasoningPort` adapters.

Two interchangeable implementations of the same port:

* :class:`~backstop.adapters.minimax.minimax_adapter.MiniMaxReasoningAdapter` —
  the REAL adapter calling MiniMax's chat-completions REST API (vendor I/O,
  ``httpx`` imported lazily inside methods).
* :class:`~backstop.adapters.minimax.local_reasoning_adapter.LocalReasoningAdapter`
  — the SIM adapter: a genuine offline grounded-NLG + denial-classification
  engine (no network, deterministic), usable as the test double and the runtime
  fallback.

Both honour the identical port contract: ``compose_line`` caps the line at
``max_words`` and cites only supplied evidence ids (ungrounded -> safe
fallback), and ``interpret_denial`` returns category/route/dialog-act drawn from
the domain enums.
"""

from __future__ import annotations

from backstop.adapters.minimax._errors import (
    MiniMaxApiError,
    MiniMaxError,
    MiniMaxParseError,
    MiniMaxTransportError,
)
from backstop.adapters.minimax.local_reasoning_adapter import LocalReasoningAdapter
from backstop.adapters.minimax.minimax_adapter import (
    MiniMaxReasoningAdapter,
    MiniMaxSettings,
)

__all__ = [
    "LocalReasoningAdapter",
    "MiniMaxReasoningAdapter",
    "MiniMaxSettings",
    "MiniMaxError",
    "MiniMaxApiError",
    "MiniMaxParseError",
    "MiniMaxTransportError",
]
