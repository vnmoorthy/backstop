"""Pure CARC/RARC lookup table.

Loads ``data/carc_rarc.json`` into an immutable in-memory table keyed by code,
where each entry exposes a ``category``, a ``canonical_reason``, and a
``default_route`` (a :class:`~backstop.domain.enums.RouteDecision`). The table
is the shared lexicon behind both denial interpretation and the routing policy.

This module performs exactly one file read at load time and is otherwise pure:
lookups are deterministic dictionary access with no I/O. The JSON file itself is
a separate deliverable; its path is resolved relative to the repository's
``data/`` directory and may be overridden for tests.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping, Optional

from backstop.domain.enums import RouteDecision

__all__ = ["CarcEntry", "CarcTable", "load_carc_table", "default_data_path"]


@dataclass(frozen=True)
class CarcEntry:
    """One row of the CARC/RARC table.

    ``code`` is the CARC (or RARC) string; ``category`` groups codes for
    analytics; ``canonical_reason`` is a clean human-readable denial reason; and
    ``default_route`` is the disposition the routing policy starts from before
    applying any RARC-specific overrides.
    """

    code: str
    category: str
    canonical_reason: str
    default_route: RouteDecision


@dataclass(frozen=True)
class CarcTable:
    """Immutable, deterministic lookup over CARC/RARC entries."""

    entries: Mapping[str, CarcEntry]

    def get(self, code: str) -> Optional[CarcEntry]:
        """Return the entry for ``code``, or ``None`` if unknown."""
        return self.entries.get(code)

    def require(self, code: str) -> CarcEntry:
        """Return the entry for ``code`` or raise :class:`KeyError` if absent."""
        entry = self.entries.get(code)
        if entry is None:
            raise KeyError(f"unknown CARC/RARC code: {code}")
        return entry

    def __contains__(self, code: object) -> bool:
        """Return ``True`` if ``code`` is a known string key."""
        return isinstance(code, str) and code in self.entries


def default_data_path() -> Path:
    """Resolve the default path to ``data/carc_rarc.json``.

    The layout is ``orchestrator/backstop/domain/carc_table.py`` with ``data/``
    at the repository root, i.e. four levels up from this file.
    """
    return Path(__file__).resolve().parents[3] / "data" / "carc_rarc.json"


def load_carc_table(path: Optional[Path] = None) -> CarcTable:
    """Load and parse the CARC/RARC JSON into a :class:`CarcTable`.

    Args:
        path: Optional override for the JSON file location. Defaults to
            :func:`default_data_path`.

    Returns:
        A frozen :class:`CarcTable` ready for pure lookups.

    Raises:
        FileNotFoundError: If the JSON file does not exist.
        ValueError: If an entry's ``default_route`` is not a valid
            :class:`~backstop.domain.enums.RouteDecision`.
    """
    source = path if path is not None else default_data_path()
    raw = json.loads(source.read_text(encoding="utf-8"))

    entries: Dict[str, CarcEntry] = {}
    for code, row in raw.items():
        try:
            route = RouteDecision(row["default_route"])
        except ValueError as exc:  # pragma: no cover - defensive
            raise ValueError(
                f"invalid default_route for code {code!r}: "
                f"{row.get('default_route')!r}"
            ) from exc
        entries[code] = CarcEntry(
            code=code,
            category=row["category"],
            canonical_reason=row["canonical_reason"],
            default_route=route,
        )
    return CarcTable(entries=entries)
