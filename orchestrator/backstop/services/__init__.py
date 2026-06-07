"""Application service layer (L3 use-cases).

Each module here orchestrates a single use-case purely over the L2 port
protocols in :mod:`backstop.ports` and the pure domain in
:mod:`backstop.domain`. Services construct nothing concrete: every collaborator
is an injected port, so the same service runs identically against a real or sim
adapter and is unit-tested against in-test fake ports. No service imports a
vendor SDK or a concrete adapter, and no service touches the wall clock,
identifier generation, or I/O except through an injected port.
"""

from __future__ import annotations
