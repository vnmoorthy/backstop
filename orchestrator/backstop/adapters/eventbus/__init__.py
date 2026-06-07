"""Event-bus adapters: redaction-typed fan-out to dashboard subscribers.

Houses :class:`~backstop.adapters.eventbus.ws_event_bus_adapter.WsEventBusAdapter`,
which implements :class:`backstop.ports.event_bus_port.EventBusPort` with an
in-process, back-pressured pub/sub. Because the published body is structurally
typed :class:`~backstop.domain.redacted.RedactedText`, raw PHI cannot reach a
subscriber.
"""

from __future__ import annotations
