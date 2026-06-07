"""System adapters: the injected clock, id generator and task supervisor.

These are the small, vendor-free infrastructure adapters that satisfy
:class:`backstop.ports.clock_port.ClockPort`,
:class:`backstop.ports.id_gen_port.IdGenPort` and
:class:`backstop.ports.task_supervisor_port.TaskSupervisorPort`. Each lives in
its own module (one responsibility per file) and depends only on the standard
library.
"""

from __future__ import annotations
