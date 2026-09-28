"""Intake — one deterministic node, no model call.

Folds today's `Resolve` (env/service, a table lookup, never a model guess)
plus the deterministic (regex/table/retrieval) parts of extraction into one
node, producing an `IntakeContext`. Ticket 03 on board
`the-graph-becomes-a-loop`; see
`.scratch/the-graph-becomes-a-loop/issues/03-what-intake-gathers-and-the-shape-the-loop-returns.md`.

**Wired in ticket 6** ("Rewire graph; drop extraction"), which also folded
this node's own `Placement` into `friday.sdk.sources.Placement` — the single
type now, carrying the superset of what `Resolve`'s old `Placement` + its
separate `project` dict and this module's own richer shape each held.

**The service fork — (c)→(a) hybrid, decided.** `request_text` is
string-matched against the room's known service names (no model, no
aliases table exists yet — see the module's own note below). Exactly one
match resolves it, filling the same cluster/namespace/pod fields `Resolve`
does today. Zero or several matches leave `service=""` and hand back the
room's whole candidate set instead — the model never invents a service, it
only ever selects from what this node already found in the room's own rows.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from friday.sdk.sources import Placement
from friday.sdk.workflow import DAGState, Node, envelope
from plugins.devops.graph.deps import ApiIssueDeps
from plugins.devops.graph.logs import _reported_at
from plugins.devops.graph.resolve import domain_of, environment_of
from plugins.devops.memory import DEVOPS_ENVIRONMENT, DEVOPS_PROJECT, DEVOPS_SERVICE

__all__ = ["Hints", "IntakeContext", "Placement", "intake_node", "intake_of"]

#: Same pattern `ApiIssueParams._RULES["correlation_id"]` validates against
#: (`plugins/devops/params.py`) — a uuid, unanchored here since it is being
#: searched for in prose rather than validated as a whole field.
_UUID = re.compile(
    r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}"
)
#: `[artifact <id>: <description>]`, as `friday/store/_common.py`'s
#: `_ARTIFACT_REF` writes it, plus the description — `_describe_artifact`
#: labels a curl's own artifact "a curl, ...", which is the only labelling
#: this node can read without a model.
_ARTIFACT_REF = re.compile(r"\[artifact ([0-9a-f]+): ([^\]]*)\]")

#: A simple count bound, not a token estimator — extraction's own budgeting
#: is out of scope for this node (spec: "keep it simple, cap the counts").
_MEMORY_CAP = 20
_SKILLS_CAP = 10


@dataclass(frozen=True, slots=True)
class Hints:
    """Cheap, deterministic pulls from `request_text` — regex/artifact ids
    only, no model. What endpoint, what's wrong, is the loop's job."""

    correlation_id: str | None = None
    curl_artifact_id: str | None = None
    response_artifact_id: str | None = None


@dataclass(frozen=True, slots=True)
class IntakeContext:
    """Intake's whole output. Never checkpointed; `placement_identity` is
    the staleness anchor a running investigation is discarded against."""

    request_text: str
    reported_at: str
    placement: Placement
    hints: Hints
    memory: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    related_tasks: tuple[str, ...] = ()

    @property
    def placement_identity(self) -> tuple:
        p = self.placement
        return (p.env, p.service, p.clone_path, p.repo_path, p.release_tag)


def _listed(value: Any) -> Any:
    """`asdict` keeps a tuple a tuple; `DAGState.to_dict`'s round trip needs
    a list (board `read-it-the-way-the-operator-does`'s own convention, see
    `logs.py`'s `frames=[[file, line] for ...]`). Applied once, generically,
    because `IntakeContext` carries tuples at two levels."""
    if isinstance(value, tuple):
        return [_listed(v) for v in value]
    if isinstance(value, dict):
        return {k: _listed(v) for k, v in value.items()}
    return value


def _hints_of(text: str) -> Hints:
    uuid = _UUID.search(text)
    refs = _ARTIFACT_REF.findall(text)
    curl_id: str | None = None
    others: list[str] = []
    for artifact_id, description in refs:
        if curl_id is None and description.strip().lower().startswith("a curl"):
            curl_id = artifact_id
        else:
            others.append(artifact_id)
    # No labelled curl — the transcript's first artifact stands in for one.
    # A limitation of reading without the extractor, not a guess: it is
    # still an id the reporter actually pasted.
    if curl_id is None and others:
        curl_id = others.pop(0)
    return Hints(
        correlation_id=uuid.group(0) if uuid else None,
        curl_artifact_id=curl_id,
        response_artifact_id=others[0] if others else None,
    )


def _service_match(text: str, services: list[Any]) -> Any | None:
    """The one service `text` names, or `None`. Matched as a **whole token**,
    not a substring: a name must be bounded by something that is not part of an
    identifier (`[a-z0-9._-]`), so a short name like `api` does NOT match inside
    the host `api-reelme-v2.dev` and cannot silently resolve the wrong service.
    A name that is a prefix of another (`backend-reelme-v2` vs
    `backend-reelme-v2-mirror`) matches only its own exact token. No alias table
    exists yet on `devops.service` (`ServiceData` in `plugins/devops/memory.py`),
    so this matches the canonical `name` only — aliases are ticket-6 work."""
    said = text.lower()
    matched = [
        row
        for row in services
        if re.search(
            rf"(?<![a-z0-9._-]){re.escape(row.name.lower())}(?![a-z0-9._-])", said
        )
    ]
    return matched[0] if len(matched) == 1 else None


async def _placement(
    channel_id: str, request_text: str, deps: ApiIssueDeps
) -> Placement:
    domain = domain_of(request_text)
    known_env = await deps.db.structured_memories(channel_id, kind=DEVOPS_ENVIRONMENT)
    env = environment_of(domain, known_env)

    services = await deps.db.structured_memories(channel_id, kind=DEVOPS_SERVICE)
    resolved = _service_match(request_text, services)

    fields: dict[str, Any] = {"service": ""}
    if resolved is not None:
        fields["service"] = resolved.name
        if env == "production":
            fields["cluster"] = resolved.prod.cluster
            fields["namespace"] = resolved.prod.namespace
            fields["app"] = resolved.prod.app
        elif env == "dev":
            fields["namespace"] = resolved.dev.namespace
            fields["pod_pattern"] = resolved.dev.pod_pattern
        project = await deps.db.structured_memory(
            channel_id, kind=DEVOPS_PROJECT, key=resolved.project
        )
        if project is not None:
            fields["clone_path"] = project.repo_path or ""
            fields["repo_path"] = project.repo_path or ""
            fields["error_code_doc"] = project.error_codes_doc or ""
            fields["stack"] = project.stack or ""
    else:
        # Vague, or unnamed: the candidate set, for the loop to narrow by
        # reading — never a guess (the (c)->(a) hybrid, ticket 03).
        fields["candidates"] = tuple(row.name for row in services)

    return Placement(env=env, container_roots=deps.container_roots, **fields)


def intake_node() -> Node:
    """Build the node. No model: every field here is a rule, a row, or a
    regex."""

    async def _intake(state: DAGState, deps: ApiIssueDeps) -> Any:
        channel_id = deps.task.conversation.channel_id
        request_text = await deps.db.original_text_for(deps.task.id) or ""
        reported_at = _reported_at(deps.task).isoformat()

        placement = await _placement(channel_id, request_text, deps)
        hints = _hints_of(request_text)

        records = await deps.db.diagnose_memories(
            channel_id, service=placement.service or None, text=request_text
        )
        memory = tuple(r.text for r in records if r.kind != "skill")[:_MEMORY_CAP]
        skills = tuple(r.text for r in records if r.kind == "skill")[:_SKILLS_CAP]
        related_tasks = tuple(r.text for r in records if r.kind == "finding")

        context = IntakeContext(
            request_text=request_text,
            reported_at=reported_at,
            placement=placement,
            hints=hints,
            memory=memory,
            skills=skills,
            related_tasks=related_tasks,
        )
        return envelope("ok", "", intake=_listed(asdict(context)))

    return Node("intake", _intake)  # type: ignore[arg-type]  # ApiIssueDeps subtype; see acknowledge.py


def intake_of(result: Any) -> IntakeContext:
    """Intake's envelope read back — the JSON round trip, undone: lists
    become tuples again, at both levels `Placement`/`IntakeContext` carry
    one."""
    data = result["intake"]
    placement_data = dict(data["placement"])
    for field_name in ("dbs", "container_roots", "candidates"):
        placement_data[field_name] = tuple(placement_data[field_name])
    return IntakeContext(
        request_text=data["request_text"],
        reported_at=data["reported_at"],
        placement=Placement(**placement_data),
        hints=Hints(**data["hints"]),
        memory=tuple(data["memory"]),
        skills=tuple(data["skills"]),
        related_tasks=tuple(data["related_tasks"]),
    )
