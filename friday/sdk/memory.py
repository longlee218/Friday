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

from dataclasses import dataclass, field
from typing import Literal

from friday.domain.models import MemoryOrigin

#: Who is answerable for a written row. The canonical value type lives in
#: `friday.domain` (the value layer beneath the sdk); re-exported here as the
#: name the spec reads, so a plugin imports it from `friday.sdk` like the rest of
#: the contract.
#:
#: **This is the one interim edge from the sdk into domain that ticket 10 leans
#: on, and it is owed to a later step.** DESIGN-v2 §9.2 makes `Origin` a native
#: sdk citizen and §13 moves domain *under* the kernel — reversing this edge —
#: when the domain-move step lands. Until then domain is the single source of the
#: origin values, so re-exporting keeps one definition rather than a second that
#: could drift from it. The dependency rule allows the sdk→domain edge for
#: exactly this reason (and for the ticket-06 workflow port's `Action` types).
Origin = MemoryOrigin

__all__ = ["MemoryKindSpec", "Origin"]


@dataclass(frozen=True)
class MemoryKindSpec:
    """One memory kind, as its plugin declares it.

    `name` is the kind's id (`fact` for a core kind, `devops.service` for a pack
    kind). `data` is the structured payload's type, validated with `fits` on
    write, or `None` for prose. `writers` are the origins allowed to write it —
    a model-origin call may not write a kind reserved for the operator.
    `cardinality` is whether a natural key holds one active row (`one-per-key`)
    or accumulates (`append`). `injected` is whether the kind is rendered into a
    prompt section (behind the trust boundary) rather than read only through a
    tool or by code.
    """

    name: str
    data: type | None = None
    writers: frozenset[Origin] = field(default_factory=lambda: frozenset({Origin.ADMIN}))
    cardinality: Literal["one-per-key", "append"] = "one-per-key"
    injected: bool = False
