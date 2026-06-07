"""Pure domain layer for the Backstop appeals kernel.

Every module in this package is side-effect free: it imports nothing outside
the standard library (and sibling domain modules), performs no I/O, and pulls
in no vendor SDKs. This is the shared kernel of entities, value objects,
enums, errors, and pure policy/transition functions on which every other layer
depends.
"""

from __future__ import annotations
