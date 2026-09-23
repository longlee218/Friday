"""The one place a memory kind is known — filled by `register()`, read by the rest.

Ticket 12 folds the hardcoded memory-kind maps (`_READERS`, `_WRITERS`,
`MEMORY_DATA`) and the `MemoryKind` closed enum into a `MemoryKindSpec` registry.
A kind now *registers itself* — a `MemoryKindSpec` (name, data, writers,
cardinality, injected) handed to `register_memory_kind` at boot — and the store,
the board and the injection path read the registry instead of a table beside the
kind.

**Readers come from the reader's side** (DESIGN-v2 §9.2): the kind no longer
names its readers. Each reader (an agent, or `"code"`) declares the kinds it
needs through `register_reader`, and `readers_for`/`domain_kinds` derive from
that — the reverse of the old `_READERS`, so a kind and its readers cannot
disagree and the kind names nothing above it.

The registry lives here, above `friday.domain` (it holds `MemoryKindSpec`, an
`sdk` type, and references domain's `*Data` classes). `friday.domain` never
imports it — the value layer stays at the bottom; the store and the board reach
*up* to the registry, never the reverse.

`MemoryKind` is a **validated string** now: `validate_kind` is the closed-set
check the enum's constructor used to give, against the registered set rather than
a compiled-in one. The name constants below are readable aliases for the strings,
not a second closed set — the registry is the authority. `ModelMemoryKind`
(the five kinds a model writes) stays a closed enum in `friday.domain`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields as dataclass_fields
from typing import Any

from friday.domain.models import (
    DecisionData,
    DependencyData,
    EnvironmentData,
    FindingData,
    MemoryRefused,
    PersonData,
    ProjectData,
    RouteData,
    RunbookData,
    ServiceData,
    SummaryData,
)
from friday.sdk.memory import MemoryKindSpec, Origin

# ── kind names ───────────────────────────────────────────────────────────────
# Readable aliases for the kind strings; the registry (not this list) is the
# authority for what is valid. Core kinds stay unprefixed (every install has
# them); the structured pack kinds move to the devops plugin in ticket 14.
FACT = "fact"
CONSTRAINT = "constraint"
DECISION = "decision"
VOICE = "voice"
FINDING = "finding"
RUNBOOK = "runbook"
SUMMARY = "summary"
PROJECT = "project"
SERVICE = "service"
ROUTE = "route"
ENVIRONMENT = "environment"
DEPENDENCY = "dependency"
PERSON = "person"

__all__ = [
    "append_kinds",
    "clear",
    "cardinality_of",
    "data_of",
    "domain_kinds",
    "injected_of",
    "kinds",
    "named_by",
    "names_in",
    "natural_key",
    "readers_for",
    "register_all_memory_kinds",
    "register_memory_kind",
    "register_reader",
    "specs",
    "validate_kind",
    "writers_for",
]

_SPECS: dict[str, MemoryKindSpec] = {}
#: Reader name (an agent, or `"code"`) -> the kinds it reads. Filled by
#: `register_reader`; `readers_for` is its transpose.
_READER_NEEDS: dict[str, frozenset[str]] = {}


def register_memory_kind(spec: MemoryKindSpec) -> None:
    """Add one memory kind. A duplicate name refuses — two producers for one id
    is the drift this registry exists to rule out."""
    if spec.name in _SPECS:
        raise ValueError(f"memory kind {spec.name!r} is already registered")
    _SPECS[spec.name] = spec


def register_reader(name: str, needs: frozenset[str]) -> None:
    """Declare the kinds one reader (an agent, or `"code"`) reads. The reverse of
    the old `_READERS`: readers name their kinds, kinds name no readers."""
    _READER_NEEDS[name] = frozenset(needs)


def clear() -> None:
    """Forget every registered kind and reader — the composition root and the
    test fixture refill before anything runs."""
    _SPECS.clear()
    _READER_NEEDS.clear()


def specs() -> Mapping[str, MemoryKindSpec]:
    return dict(_SPECS)


def kinds() -> frozenset[str]:
    """Every registered kind name — the closed set, from the registry."""
    return frozenset(_SPECS)


def validate_kind(kind: str) -> str:
    """The kind, if it is registered; otherwise `ValueError` — the closed-set
    check the `MemoryKind` enum's constructor used to give, against the
    registered set rather than a compiled-in one."""
    if kind not in _SPECS:
        raise ValueError(f"{kind!r} is not a registered memory kind")
    return kind


def writers_for(kind: str) -> frozenset[Origin]:
    """Which origin may write a memory of this kind. `Database.memory_add`
    refuses anything else."""
    return _SPECS[validate_kind(kind)].writers


def data_of(kind: str) -> type | None:
    """This kind's `data` schema, or `None` for a prose kind. Checked against
    with `fits` at `Database.memory_add`."""
    return _SPECS[validate_kind(kind)].data


def cardinality_of(kind: str) -> str:
    """`one-per-key` (a natural key holds one active row) or `append` (rows
    accumulate). The store's partial unique index exempts the `append` kinds."""
    return _SPECS[validate_kind(kind)].cardinality


def append_kinds() -> frozenset[str]:
    """The kinds that accumulate rather than holding one active row per key — the
    kinds the store's unique index must exempt. Derived from the registry so the
    index cannot silently drift from the specs (Rule 20)."""
    return frozenset(n for n, s in _SPECS.items() if s.cardinality == "append")


def injected_of(kind: str) -> bool:
    """Whether this kind is rendered into a prompt section (prose an agent reads)
    rather than read only through a tool or by code."""
    return _SPECS[validate_kind(kind)].injected


def readers_for(kind: str) -> frozenset[str]:
    """Who reads a memory of this kind — agents by name, or `"code"` for a
    structured kind only a tool call is parameterised by. Derived from the
    reader side (`register_reader`), so a kind names no reader. Raises on a kind
    outside the registered set, the way `MemoryKind(kind)` did."""
    validate_kind(kind)
    return frozenset(reader for reader, needs in _READER_NEEDS.items() if kind in needs)


def domain_kinds() -> frozenset[str]:
    """The kinds rendered into the extractor's prompt: its declared needs that
    are `injected`. The reader side picks *which* agent reads a kind; `injected`
    decides whether that kind is rendered into a prompt section at all rather
    than read only through a tool — so a kind the extractor needed but that was
    marked tool-only would be excluded here. `db.domain_memories` loads exactly
    these. Derived, never a second enumeration (D14)."""
    return frozenset(
        k for k in _READER_NEEDS.get("extractor", frozenset()) if injected_of(k)
    )


def names_in(kind: str) -> dict[str, str]:
    """This kind's foreign keys: field name -> the kind it names. Read off
    `field(metadata={"names": ...})`, so the declaration and the check cannot
    disagree. Top level only."""
    shape = data_of(kind)
    if shape is None:
        return {}
    return {
        f.name: str(f.metadata["names"])
        for f in dataclass_fields(shape)
        if f.metadata.get("names")
    }


def named_by(kind: str) -> tuple[tuple[str, str], ...]:
    """Every `(kind, field)` that names rows of `kind` — the reverse of
    `names_in`, for asking "who would this rename break?"."""
    kind = validate_kind(kind)
    return tuple(
        (other, field)
        for other in _SPECS
        for field, named in names_in(other).items()
        if named == kind
    )


def natural_key(kind: str, data: dict[str, Any] | None, given: str | None) -> str | None:
    """A structured kind's natural key — the spec's `key=` column — read off its
    already-validated `data`, so the key cannot disagree with the row.

    `runbook` is the one kind whose key is not in its data (a short name the
    operator gives), so it is the one that takes `given` and refuses without it.
    `summary` has a fixed key, which is what makes the partial unique index hold
    it to one per room. Prose kinds and `decision` have none.

    The per-kind branches stay hardcoded for now: a key-derivation field on the
    spec is a later trigger (§9.2), and the pack kinds move to the devops plugin
    with this logic in ticket 14.
    """
    kind = validate_kind(kind)
    d = data or {}
    if kind == RUNBOOK:
        if not (given or "").strip():
            raise MemoryRefused("a runbook needs a key — a short name for it")
        return given.strip()
    return {
        FINDING: lambda: f"{d.get('service')}:{d.get('error_code') or ''}",
        SUMMARY: lambda: "room",
        PROJECT: lambda: d.get("name"),
        SERVICE: lambda: d.get("name"),
        ROUTE: lambda: d.get("domain"),
        ENVIRONMENT: lambda: d.get("suffix"),
        DEPENDENCY: lambda: f"{d.get('from_service')}->{d.get('to_service')}",
        PERSON: lambda: d.get("discord_id"),
    }.get(kind, lambda: None)()


# ── the core kind set, and who reads it ──────────────────────────────────────
# The catalog that was `_WRITERS` + `MEMORY_DATA` + `_READERS`, now each kind
# registering itself. `injected` is true for the prose kinds an agent reads and
# false for the structured kinds only code is parameterised by; `cardinality` is
# `append` for `finding` (every diagnosis of a known fault is worth keeping) and
# `one-per-key` for the rest. Ticket 14 moves the structured pack kinds and this
# routing into the devops plugin.
_MODEL_AND_ADMIN = frozenset(Origin)
_MODEL = frozenset({Origin.MODEL})
_ADMIN = frozenset({Origin.ADMIN})


def register_all_memory_kinds() -> None:
    """Register the core + pack kinds and the reader routing — the composition
    root and the test fixture call this. Idempotent via `clear` first."""
    clear()
    prose = dict(cardinality="one-per-key", injected=True)
    for spec in (
        MemoryKindSpec(name=FACT, data=None, writers=_MODEL_AND_ADMIN, **prose),
        MemoryKindSpec(name=CONSTRAINT, data=None, writers=_MODEL_AND_ADMIN, **prose),
        MemoryKindSpec(name=DECISION, data=DecisionData, writers=_MODEL_AND_ADMIN, **prose),
        MemoryKindSpec(name=VOICE, data=None, writers=_MODEL_AND_ADMIN, **prose),
        MemoryKindSpec(name=FINDING, data=FindingData, writers=_MODEL,
                       cardinality="append", injected=True),
        MemoryKindSpec(name=SUMMARY, data=SummaryData, writers=_MODEL, **prose),
        MemoryKindSpec(name=RUNBOOK, data=RunbookData, writers=_ADMIN, **prose),
        MemoryKindSpec(name=PROJECT, data=ProjectData, writers=_ADMIN,
                       cardinality="one-per-key", injected=False),
        MemoryKindSpec(name=SERVICE, data=ServiceData, writers=_ADMIN,
                       cardinality="one-per-key", injected=False),
        MemoryKindSpec(name=ROUTE, data=RouteData, writers=_ADMIN,
                       cardinality="one-per-key", injected=False),
        MemoryKindSpec(name=ENVIRONMENT, data=EnvironmentData, writers=_ADMIN,
                       cardinality="one-per-key", injected=False),
        MemoryKindSpec(name=DEPENDENCY, data=DependencyData, writers=_ADMIN,
                       cardinality="one-per-key", injected=False),
        MemoryKindSpec(name=PERSON, data=PersonData, writers=_ADMIN,
                       cardinality="one-per-key", injected=False),
    ):
        register_memory_kind(spec)

    # Readers name their kinds (the transpose of the old `_READERS`).
    register_reader("extractor", frozenset({FACT, CONSTRAINT, DECISION, FINDING}))
    register_reader("diagnose", frozenset({FACT, CONSTRAINT, DECISION, FINDING, RUNBOOK}))
    register_reader("responder", frozenset({VOICE, SUMMARY}))
    register_reader("triage", frozenset({SUMMARY}))
    register_reader(
        "code", frozenset({PROJECT, SERVICE, ROUTE, ENVIRONMENT, DEPENDENCY, PERSON})
    )
