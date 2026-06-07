"""Composition root: ``build_container(settings)`` (skeleton).

The ONE impure graph. It constructs the shared cross-cutting singletons first
(redaction, audit, cost, clock, id-gen, task supervisor), then builds every
capability adapter by mode via ``adapter_factory``, then assembles the services
(which depend only on ports), and finally returns the immutable
:class:`Container`.

Today this returns an empty (all-``None``) ``Container`` so the M0.3 shell
type-checks and imports cleanly; the body below documents the intended ordering
and raises :class:`NotImplementedError` for the slots that WS-Integration fills.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from backstop.composition.container import Container

if TYPE_CHECKING:  # pragma: no cover - typing only
    from backstop.infra.config import Settings


def build_container(settings: Settings) -> Container:
    """Build the fully-wired immutable :class:`Container` from ``settings``.

    Intended ordering (filled in by WS-Integration):

    1. Shared singletons: ``clock``, ``id_gen``, HTTP client, DB handle,
       ``redaction`` (sole ``RedactedText`` producer), ``audit``, ``cost``,
       ``tasks``.
    2. Capability ports via ``adapter_factory.make_<port>(settings, shared)``,
       selecting real-vs-sim by ``settings.mode_for(port)``; PAVO tries torch
       then numpy.
    3. Services, each receiving only ports.
    4. Assemble and freeze into a :class:`Container`.

    Args:
        settings: The frozen application settings.

    Returns:
        The immutable wired container.
    """
    # M0.3: an empty shell so the container type-checks and imports cleanly.
    # WS-Integration replaces this with the real graph (the per-slot factory
    # calls below currently raise NotImplementedError and are intentionally
    # unreachable until then).
    return Container(settings=settings)
