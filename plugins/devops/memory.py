"""The devops pack kinds — the rows this plugin's graph reads to route a report.

Moved out of `friday.domain.models` + `friday.memory.registry` in ticket 14: a
pack kind ships with the plugin that reads it, namespaced `devops.*`, and it
carries its own schema, its natural-key derivation (`MemoryKindSpec.key`) and its
reader routing here rather than in a per-kind branch the kernel would have to
carry. `person` and `finding` stay core (every install has them); the five
structured devops kinds are these.

`data` is checked against the schema with `friday.agent.structured.fits` at the
one write path; every field a spec line marks optional (`?`) has a default.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from friday.sdk import MemoryKindSpec, Origin

__all__ = [
    "DEVOPS_DEPENDENCY",
    "DEVOPS_ENVIRONMENT",
    "DEVOPS_KINDS",
    "DEVOPS_MEMORY_KINDS",
    "DEVOPS_PROJECT",
    "DEVOPS_ROUTE",
    "DEVOPS_SERVICE",
    "DEPENDENCY_READERS",
    "DbCheck",
    "DependencyData",
    "DevPlacement",
    "EnvironmentData",
    "ProdPlacement",
    "ProjectData",
    "RouteData",
    "ServiceData",
]

# ── kind names ───────────────────────────────────────────────────────────────
DEVOPS_PROJECT = "devops.project"
DEVOPS_SERVICE = "devops.service"
DEVOPS_ROUTE = "devops.route"
DEVOPS_ENVIRONMENT = "devops.environment"
DEVOPS_DEPENDENCY = "devops.dependency"

#: Every devops pack kind, in one set — for the boot check and the
#: dependency-rule test's "pack kinds are `devops.*`" expectation.
DEVOPS_KINDS = frozenset(
    {DEVOPS_PROJECT, DEVOPS_SERVICE, DEVOPS_ROUTE, DEVOPS_ENVIRONMENT, DEVOPS_DEPENDENCY}
)


# ── one schema per structured kind ───────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class ProjectData:
    name: str
    #: **Picked, not spelled** (ticket 19). A path on the operator's own
    #: machine whose typo reads exactly like a correct one: the row looks
    #: right in the form and `ReadFailingCode` quietly reads nothing.
    repo_path: str = field(metadata={"picks": "directory"})
    default_branch: str
    stack: str
    docs_paths: list[str] = field(default_factory=list)
    error_codes_doc: str | None = None


@dataclass(frozen=True, slots=True)
class ProdPlacement:
    cluster: str
    namespace: str
    app: str


@dataclass(frozen=True, slots=True)
class DevPlacement:
    kube_context: str
    namespace: str
    pod_pattern: str


@dataclass(frozen=True, slots=True)
class ServiceData:
    name: str
    #: **`names` is a foreign key, declared where the field is.** The store
    #: matches this against a `devops.project` row's own key by string
    #: equality. Declared here so the form offers the rows that exist and a
    #: later check can refuse one that does not, from one statement.
    project: str = field(metadata={"names": DEVOPS_PROJECT})
    prod: ProdPlacement
    dev: DevPlacement


@dataclass(frozen=True, slots=True)
class EnvironmentData:
    """What a domain suffix means: ours, and which environment.

    **Longest suffix wins**, which is what lets one table hold a rule and its
    exceptions with no branch for either. A domain no row matches is
    **external** — not ours, and the graph ends there promising nothing.
    """

    suffix: str
    env: Literal["dev", "production"]


@dataclass(frozen=True, slots=True)
class RouteData:
    #: The exact host. `devops.environment` answers "ours, and which" for a
    #: family of hosts; this answers "which service" for one. `env` is carried
    #: here too, and the redundancy is deliberate: `Resolve` refuses when the
    #: two rows disagree rather than picking.
    domain: str
    env: Literal["dev", "production"]
    #: The same foreign key as `ServiceData.project`, and the same reason.
    service: str = field(metadata={"names": DEVOPS_SERVICE})


@dataclass(frozen=True, slots=True)
class DbCheck:
    table: str
    key_column: str
    state_column: str


@dataclass(frozen=True, slots=True)
class DependencyData:
    from_service: str
    to_service: str
    via: Literal["http", "queue", "webhook"]
    join_key: str
    db_checks: list[DbCheck] = field(default_factory=list)


# ── the specs, with their natural-key derivation ─────────────────────────────
_ADMIN = frozenset({Origin.ADMIN})


def _by(field_name: str):
    """A `MemoryKindSpec.key` reading one field off the row's data — the
    `name`/`domain`/`suffix` a `one-per-key` kind is unique on."""
    return lambda data, _given: (data or {}).get(field_name)


def _dependency_key(data, _given):
    d = data or {}
    return f"{d.get('from_service')}->{d.get('to_service')}"


DEVOPS_MEMORY_KINDS = (
    MemoryKindSpec(
        name=DEVOPS_PROJECT, data=ProjectData, writers=_ADMIN,
        cardinality="one-per-key", injected=False, key=_by("name"),
    ),
    MemoryKindSpec(
        name=DEVOPS_SERVICE, data=ServiceData, writers=_ADMIN,
        cardinality="one-per-key", injected=False, key=_by("name"),
    ),
    MemoryKindSpec(
        name=DEVOPS_ROUTE, data=RouteData, writers=_ADMIN,
        cardinality="one-per-key", injected=False, key=_by("domain"),
    ),
    MemoryKindSpec(
        name=DEVOPS_ENVIRONMENT, data=EnvironmentData, writers=_ADMIN,
        cardinality="one-per-key", injected=False, key=_by("suffix"),
    ),
    MemoryKindSpec(
        name=DEVOPS_DEPENDENCY, data=DependencyData, writers=_ADMIN,
        cardinality="one-per-key", injected=False, key=_dependency_key,
    ),
)

#: The reader routing this plugin adds: `code` reads every devops kind (a
#: tool call is parameterised by them), and the `devops.diagnose` agent reads
#: the core prose kinds plus `skill`. Merged with the core routing at boot, so
#: a devops kind names its readers without the kernel naming a devops kind.
DEPENDENCY_READERS = {
    "code": DEVOPS_KINDS,
    "devops.diagnose": frozenset(
        {"fact", "constraint", "decision", "finding", "skill"}
    ),
}
