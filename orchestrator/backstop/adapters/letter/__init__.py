"""Letter adapters: deterministic, markup-escaping PDF render of an appeal letter.

Houses :class:`~backstop.adapters.letter.reportlab_letter_adapter.ReportlabLetterAdapter`,
which implements :class:`backstop.ports.letter_render_port.LetterRenderPort` by
rendering a fully-redacted :class:`~backstop.ports.letter_render_port.RedactedAppealLetter`
to deterministic PDF bytes with every field markup-escaped. The ``reportlab``
library is imported lazily inside the render path.
"""

from __future__ import annotations
