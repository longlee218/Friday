"""The memory contract a plugin registers a kind against.

Contracts only — a dataclass and the origin value it carries, no I/O. A plugin
ships its pack kinds by handing `MemoryKindSpec`s to `PluginAPI.memory_kind`;
the kernel's one write path reads the spec to know a kind's shape, who may write
it, whether it is one-per-key or appended, and whether it is rendered into a
prompt. Ticket 10 defines the spec; ticket 12 fills the registry from it.

**Trimmed to the fields the registry uses today** (spec § Registration and
scope, Rule 20): `name`, `data`, `writers`, `cardinality`, `injected`. The
DESIGN-v2 §9.2 fields not yet used — `schema_version`/`upgrade`/`max_chars`/
`sensitivity`/`allowed_scopes`/`audience`/provider-policy — are added on their
trigger, each with a test, so no inert field pretends to be a rule.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

__all__ = ["MemoryKindSpec", "MemoryOrigin", "Origin"]


class MemoryOrigin(StrEnum):
    """Who is answerable for a memory row. `ADMIN` is the operator, through
    the board's own routes; a model-origin call may not update, supersede or
    delete an `ADMIN` row, and is told "no such memory" rather than why.

    A native `sdk` citizen (DESIGN-v2 §9.2, §13): the sdk is the bottom of the
    stack now, so the origin values a `MemoryKindSpec` names live here, and the
    kernel's models import them from the sdk like everything else."""

    MODEL = "model"
    ADMIN = "admin"


#: The name the spec reads for the same value type, so a plugin can write
#: `writers=frozenset({Origin.ADMIN})` against the sdk surface.
Origin = MemoryOrigin


@dataclass(frozen=True)
class MemoryKindSpec:
    """One memory kind, as its plugin declares it.

    `name` is the kind's id (`fact` for a core kind, `backend.service` for a pack
    kind). `data` is the structured payload's type, validated with `fits` on
    write, or `None` for prose. `writers` are the origins allowed to write it —
    a model-origin call may not write a kind reserved for the operator.
    `cardinality` is whether a natural key holds one active row (`one-per-key`)
    or accumulates (`append`). `injected` is whether the kind is rendered into a
    prompt section (behind the trust boundary) rather than read only through a
    tool or by code.

    `key` derives a structured kind's natural key from its `(data, given)` — the
    one row a `one-per-key` kind holds. It moved onto the spec in ticket 14 so a
    plugin ships its kinds' key derivation with the plugin, rather than the
    kernel carrying a per-kind branch that names `backend.service` (DESIGN-v2 §9.2,
    the trigger the memory registry anticipated). `None` for a prose kind, or a
    kind whose row carries no natural key.
    """

    name: str
    data: type | None = None
    writers: frozenset[Origin] = field(default_factory=lambda: frozenset({Origin.ADMIN}))
    cardinality: Literal["one-per-key", "append"] = "one-per-key"
    injected: bool = False
    #: `(data, given) -> key`, where `data` is the row's validated payload and
    #: `given` an operator-supplied name (only `skill` uses `given`). Read by
    #: `friday.kernel.memory.registry.natural_key`.
    key: Callable[[dict | None, str | None], str | None] | None = None
