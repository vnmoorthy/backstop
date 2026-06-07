"""Revenue-cycle core: the deployable, billable loop.

Turns a real payer remittance (X12 835 ERA) into a recoverable worklist, and
turns appeal outcomes into recovered dollars and a contingency invoice. This is
the part that makes Backstop a product a design partner deploys and pays for on
recovered dollars — not a demo.
"""
from __future__ import annotations
