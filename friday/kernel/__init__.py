"""The kernel: it owns the invariants and names no plugin.

Ticket 10 lays the skeleton — the `Registry` every plugin registers into, importing
only `friday.sdk`. The kernel's other pieces (the chain, the deps builder, the
harness) move in as their steps land (DESIGN-v2 §13). Two rules a test holds in
place from here on: the kernel imports `sdk` and nothing higher, and it contains
no plugin import and no task-type or pack-kind literal.
"""

from friday.kernel.registry import DuplicateRegistration, Registry

__all__ = ["DuplicateRegistration", "Registry"]
