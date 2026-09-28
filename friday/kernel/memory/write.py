"""The memory write path: the kernel enforces the trust-boundary invariants
before the store persists a row (ticket 16).

The store's memory writers are **dumb about these two invariants** now. They
still do their own data integrity — schema-fit (`fits`), the per-channel cap,
referential checks (dangling/orphaning), the admin-row access filter, the unique
natural key — but the *trust boundary* lives here, not inside the store:

- a line shaped like an instruction at this system's own mechanism is refused
  (`check_not_instruction_shaped`, D25 / CONTEXT rule 8), and
- an origin may write only a kind its `writers` allow — a model-origin call
  cannot write an operator-only kind.

Every runtime producer of a new memory goes through these functions, so a store
that skipped the checks (or a fake one in a test) could not smuggle an
instruction-shaped line or a wrong-origin row past them — the same shape of
guarantee the outbox's approval gate gives (`friday/kernel/outbox.py`). A proposed
candidate is guarded here at propose time, so the store's own `memory_add` — the
one a candidate is resolved through — can trust the text it already holds.
"""

from __future__ import annotations

from typing import Any

from friday.kernel.domain.memory_guard import check_not_instruction_shaped
from friday.kernel.domain.memory import Memory, MemoryCandidate, MemoryRefused
from friday.sdk.memory import MemoryOrigin
from friday.kernel.domain.state import FridayState
from friday.kernel.memory import registry as memory_kinds

__all__ = ["add", "propose", "supersede", "update"]


def _guard_new_line(text: str, *, kind: str, origin: str) -> None:
    """The two trust-boundary checks a new line of memory must pass: its origin
    may write this kind, and it does not read as an instruction."""
    if MemoryOrigin(origin) not in memory_kinds.writers_for(kind):
        raise MemoryRefused(f"{kind} is not a kind {origin} may write")
    check_not_instruction_shaped(text)


async def add(
    db,
    state: FridayState,
    text: str,
    *,
    kind: str = memory_kinds.VOICE,
    origin: str = MemoryOrigin.MODEL,
    key: str | None = None,
    data: dict[str, Any] | None = None,
) -> Memory | None:
    """Guard the line, then persist it. `None` when the channel is at its cap."""
    _guard_new_line(text, kind=kind, origin=origin)
    return await db.memory_add(state, text, kind=kind, origin=origin, key=key, data=data)


async def update(
    db,
    state: FridayState,
    memory_id: str,
    text: str,
    *,
    data: dict[str, Any] | None = None,
    origin: str = MemoryOrigin.MODEL,
) -> Memory | None:
    """A correction is a new line of text, so it passes the instruction-shape
    guard the same way. The origin/admin-row check is the store's `_live_memory`,
    which needs the row to enforce it."""
    check_not_instruction_shaped(text)
    return await db.memory_update(state, memory_id, text, data=data, origin=origin)


async def supersede(
    db,
    state: FridayState,
    memory_id: str,
    text: str,
    *,
    origin: str = MemoryOrigin.MODEL,
    data: dict[str, Any] | None = None,
) -> Memory | None:
    """A new claim is a new line of text — guarded here for the same reason."""
    check_not_instruction_shaped(text)
    return await db.memory_supersede(state, memory_id, text, origin=origin, data=data)


async def propose(
    db,
    state: FridayState,
    text: str,
    *,
    kind: str = memory_kinds.VOICE,
    origin: str = MemoryOrigin.MODEL,
) -> MemoryCandidate:
    """Stage a candidate — guarded here, at the write path, so the line is
    refused before it is stored rather than when the operator later accepts it
    (the resolve path reuses this already-checked text)."""
    _guard_new_line(text, kind=kind, origin=origin)
    return await db.propose_memory(state, text, kind=kind)
