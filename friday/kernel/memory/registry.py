"""The one place a memory kind is known — filled by `register()`, read by the rest.

Ticket 12 folded the hardcoded memory-kind maps and the `MemoryKind` enum into a
`MemoryKindSpec` registry; ticket 14 moved the structured *pack* kinds
(`backend.*`) out to the backend plugin, leaving the **core** kinds here — the ones
every install has. A kind registers itself (a `MemoryKindSpec` handed to
`register_memory_kind` at boot), and the store, the board and the injection path
read the registry instead of a table beside the kind.

**Readers come from the reader's side** (DESIGN-v2 §9.2): the kind no longer
names its readers. Each reader (an agent, or `"code"`) declares the kinds it
needs through `register_reader`, and `readers_for`/`domain_kinds` derive from
that. `register_reader` **merges**, so a plugin adds its kinds to a reader the
core already routes to (`code`) without naming what the core put there.

**A kind's natural key is on its spec** (`MemoryKindSpec.key`, ticket 14): the
per-kind branch that used to live in `natural_key` moved onto the spec so a pack
kind ships its key derivation with the plugin. `natural_key` just delegates.

The registry lives above `friday.kernel.domain` (it holds `MemoryKindSpec`, an
`sdk` type, and references the domain's `*Data` classes). The domain models never
import it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields as dataclass_fields
from typing import Any

from friday.kernel.domain.memory import (
    DecisionData,
    FindingData,
    MemoryRefused,
    PersonData,
    SkillData,
    SummaryData,
)
from friday.sdk.memory import MemoryKindSpec, Origin

# ── core kind names ───────────────────────────────────────────────────────────
# Readable aliases for the kind strings; the registry (not this list) is the
# authority for what is valid. Core kinds stay unprefixed (every install has
# them); the structured pack kinds are the backend plugin's (`backend.service` …).
FACT = "fact"
CONSTRAINT = "constraint"
DECISION = "decision"
VOICE = "voice"
FINDING = "finding"
SUMMARY = "summary"
#: The procedures a diagnosis reads. Was `runbook` until ticket 14 (DESIGN-v2
#: §6.3: procedures are skills); the `skill` kind carries the same shape and the
#: same operator-given key, and a data migration renamed the rows.
SKILL = "skill"
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
    """Declare the kinds one reader (an agent, or `"code"`) reads. **Merges** —
    a plugin adds its kinds to `code` without re-declaring the core kinds routed
    there. The reverse of the old `_READERS`: readers name their kinds."""
    _READER_NEEDS[name] = _READER_NEEDS.get(name, frozenset()) | frozenset(needs)


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
    are `injected`. `db.domain_memories` loads exactly these. Derived, never a
    second enumeration (D14)."""
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
    """A kind's natural key — the one row a `one-per-key` kind holds — from its
    spec's `key` derivation (`MemoryKindSpec.key`), or `None` when it has none (a
    prose kind, `decision`). The per-kind branches that used to live here moved
    onto the specs in ticket 14, so a pack kind ships its own key logic."""
    spec = _SPECS[validate_kind(kind)]
    return spec.key(data, given) if spec.key is not None else None


# ── the core kind set, its keys, and who reads it ────────────────────────────
_MODEL_AND_ADMIN = frozenset(Origin)
_MODEL = frozenset({Origin.MODEL})
_ADMIN = frozenset({Origin.ADMIN})


def _finding_key(data: dict | None, _given: str | None) -> str:
    d = data or {}
    return f"{d.get('service')}:{d.get('error_code') or ''}"


def _skill_key(_data: dict | None, given: str | None) -> str:
    """`skill` is the one kind whose key is not in its data — a short name the
    operator gives — so it refuses without one."""
    if not (given or "").strip():
        raise MemoryRefused("a skill needs a key — a short name for it")
    return given.strip()


def register_all_memory_kinds(config: Any = None) -> None:
    """Register the core kinds and reader routing, then every configured
    plugin's pack kinds. `config` is the application `Config` whose `plugins`
    list says which to load; `None` loads the default set (`plugins.backend`) with
    default config, so a script or a test that only needs the kinds registered
    calls this with no argument. Idempotent via `clear` first."""
    clear()
    prose = dict(cardinality="one-per-key", injected=True)
    for spec in (
        MemoryKindSpec(name=FACT, data=None, writers=_MODEL_AND_ADMIN, **prose),
        MemoryKindSpec(name=CONSTRAINT, data=None, writers=_MODEL_AND_ADMIN, **prose),
        MemoryKindSpec(name=DECISION, data=DecisionData, writers=_MODEL_AND_ADMIN, **prose),
        MemoryKindSpec(name=VOICE, data=None, writers=_MODEL_AND_ADMIN, **prose),
        MemoryKindSpec(name=FINDING, data=FindingData, writers=_MODEL,
                       cardinality="append", injected=True, key=_finding_key),
        MemoryKindSpec(name=SUMMARY, data=SummaryData, writers=_MODEL,
                       key=lambda _d, _g: "room", **prose),
        MemoryKindSpec(name=SKILL, data=SkillData, writers=_ADMIN,
                       key=_skill_key, **prose),
        MemoryKindSpec(name=PERSON, data=PersonData, writers=_ADMIN,
                       cardinality="one-per-key", injected=False,
                       key=lambda d, _g: (d or {}).get("discord_id")),
    ):
        register_memory_kind(spec)

    # Readers name their kinds (the transpose of the old `_READERS`). `backend.
    # diagnose` and the backend kinds under `code` are the plugin's; it declares
    # them through `register_reader`, which merges.
    register_reader("extractor", frozenset({FACT, CONSTRAINT, DECISION, FINDING}))
    register_reader("responder", frozenset({VOICE, SUMMARY}))
    register_reader("triage", frozenset({SUMMARY}))
    register_reader("code", frozenset({PERSON}))

    from friday.kernel.plugin_host import load_plugins

    class _DefaultConfig:
        plugins = ("plugins.backend",)
        plugin_blocks: dict = {}

    loaded = load_plugins(config if config is not None else _DefaultConfig())
    for spec in loaded.registry.memory_kinds().values():
        register_memory_kind(spec)
    for reader, kinds in loaded.registry.readers().items():
        register_reader(reader, kinds)
