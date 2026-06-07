"""Sponsor integration clients.

Each client exposes a real implementation when its API key/env is present, and a
deterministic local fallback (``.mode == "sim"``) otherwise. The ``.mode`` flag is
surfaced live on the dashboard so nothing is misrepresented as real (SPEC §7, the
honesty contract). No client opens a network connection at import time.
"""
from __future__ import annotations

from .aws import AWS
from .livekit_client import LiveKit
from .minimax import MiniMax
from .moss import MossClient
from .qwen import Qwen
from .truefoundry import TrueFoundryGateway
from .unsiloed import Unsiloed

__all__ = [
    "AWS",
    "LiveKit",
    "MiniMax",
    "MossClient",
    "Qwen",
    "TrueFoundryGateway",
    "Unsiloed",
    "sponsor_modes",
    "make_clients",
]


def make_clients() -> dict:
    """Instantiate one of every sponsor client (PAVO is built separately at startup)."""
    return {
        "moss": MossClient(),
        "truefoundry": TrueFoundryGateway(),
        "unsiloed": Unsiloed(),
        "minimax": MiniMax(),
        "qwen": Qwen(),
        "livekit": LiveKit(),
        "aws": AWS(),
    }


def sponsor_modes(clients: dict | None = None) -> dict:
    """Return {sponsor_name: "real"|"sim"} for the dashboard sponsor row.

    PAVO is always "real" (the vendored TMLR weights). The other seven read their
    own .mode (real iff their key/env is present).
    """
    clients = clients if clients is not None else make_clients()
    modes = {"pavo": "real"}
    for name, client in clients.items():
        modes[name] = getattr(client, "mode", "sim")
    return modes
