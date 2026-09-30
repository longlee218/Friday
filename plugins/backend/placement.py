"""The backend domain type, `Placement`, and its enricher.

Core Intake (`friday/kernel/spine/intake.py`) hands `enrich` the seed; this
returns where the case lives and what the reporter pasted, read from the
room's own rows — DB only, no network, no model, so it re-runs every reply
pass and its identity diff stays stable (board `domains-plug-in` ticket 04).

**The service fork — (c)→(a) hybrid.** The text is string-matched against
the room's known service names. Exactly one match resolves it; zero or
several leave `service=""` and hand back the room's whole candidate set — the
model never invents a service, it only selects from what was found here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, ClassVar

from friday.sdk.intake import IntakeSeed
from friday.sdk.sources import Repo
from plugins.backend.memory import BACKEND_ENVIRONMENT, BACKEND_PROJECT, BACKEND_SERVICE
from plugins.backend.resolve import domain_of, environment_of

__all__ = ["Placement", "Project", "enrich"]


@dataclass(frozen=True, slots=True)
class Project:
    """One of the room's repositories, as a tool's `repo` argument names it
    (the `backend.project` row's key). Every code/docs tool takes one, and
    only these are readable (board `domains-plug-in` ticket 06 §5)."""

    name: str
    repo_path: str = ""
    docs_paths: tuple[str, ...] = ()
    error_codes_doc: str = ""


@dataclass(frozen=True, slots=True)
class Placement:
    """Where one case lives, and the domain's reading of the hints.

    Flattened out of a service's two halves on purpose — every reader wants
    one place, and a reader that has to remember which half to read is one
    that one day reads the other.
    """

    #: The staleness anchor: a reply that moves one of these is another case.
    #: The hints below are outside it, so a reply adding a correlationId
    #: continues.
    IDENTITY: ClassVar[tuple[str, ...]] = ("env", "service", "clone_path", "repo_path")

    #: Where a service's source sits inside its image, stripped from a stack
    #: frame before it is joined to a repo's clone (ticket 23; moved off
    #: `backend.code`'s `CONTAINER_ROOTS` constant).
    CONTAINER_ROOTS: ClassVar[tuple[str, ...]] = ("/usr/src/app", "/app", "/srv/app")

    env: str
    service: str = ""
    #: Production: the Loki labels. Dev: empty.
    cluster: str = ""
    namespace: str = ""
    app: str = ""
    #: Dev: the pod name pattern to grep for. Production: empty.
    pod_pattern: str = ""
    #: Where the operator's clone is. `clone_path`/`repo_path` are the same
    #: path today — kept as two fields because nothing yet forces them to agree.
    clone_path: str = ""
    repo_path: str = ""
    #: What the repository's own error-code doc says each code means.
    error_code_doc: str = ""
    #: The service's tech stack (e.g. "NestJS"), so a stack trace is read in
    #: that framework's idiom.
    stack: str = ""
    dbs: tuple[str, ...] = ()
    #: The room's candidate services, when `service` is unresolved.
    candidates: tuple[str, ...] = ()
    #: The first uuid the reporter pasted.
    correlation_id: str | None = None
    curl_artifact_id: str | None = None
    response_artifact_id: str | None = None
    #: The resolved service's project: the `repo` a diagnosis reads, and the
    #: one repository whose running version is known (its service's tag).
    project: str = ""
    #: The room's repositories, what a tool's `repo` may name. Outside
    #: `IDENTITY`: a project row added mid-case does not move the case.
    projects: tuple[Project, ...] = ()

    def repo(self, name: str) -> Project | None:
        """The room's project called `name`, or `None`."""
        return next((p for p in self.projects if p.name == name), None)

    def repos(self) -> tuple[Repo, ...]:
        """`core.repos`'s view of the room's projects (ticket 23) — including
        one with no clone recorded; the tools decide what to say about it."""
        return tuple(
            Repo(
                name=p.name, repo_path=p.repo_path, container_roots=self.CONTAINER_ROOTS
            )
            for p in self.projects
        )

    def retrieval_keys(self) -> dict[str, str]:
        """What memory is matched on besides the text: a runbook's
        `when.service`, a finding's `service`."""
        return {"service": self.service} if self.service else {}


def _artifacts(seed: IntakeSeed) -> tuple[str | None, str | None]:
    """(curl, response) artifact ids. The store labels a curl's own artifact
    "a curl, ..."; with none labelled, the first artifact stands in — still an
    id the reporter actually pasted, not a guess."""
    curl: str | None = None
    others: list[str] = []
    for ref in seed.hints.artifacts:
        if curl is None and ref.description.strip().lower().startswith("a curl"):
            curl = ref.id
        else:
            others.append(ref.id)
    if curl is None and others:
        curl = others.pop(0)
    return curl, (others[0] if others else None)


def _service_match(text: str, services: list[Any]) -> Any | None:
    """The one service `text` names, or `None`. Matched as a **whole token**:
    a name must be bounded by something that is not part of an identifier
    (`[a-z0-9._-]`), so `api` does not match inside `api-reelme-v2.dev`, and
    `backend-reelme-v2` does not match `backend-reelme-v2-mirror`."""
    said = text.lower()
    matched = [
        row
        for row in services
        if re.search(
            rf"(?<![a-z0-9._-]){re.escape(row.name.lower())}(?![a-z0-9._-])", said
        )
    ]
    return matched[0] if len(matched) == 1 else None


def _newest_domain(turns: tuple[str, ...]) -> str | None:
    """The host of the first URL in the newest turn that has one: a reply
    pasting a prod URL after a dev one moves the case to prod. First within a
    turn, because a curl's later URLs are header values (`domain_of`)."""
    for turn in reversed(turns):
        if (found := domain_of(turn)) is not None:
            return found
    return None


async def enrich(seed: IntakeSeed, db: Any) -> Placement:
    """The backend's enricher (`Plugin.enricher`)."""
    channel_id, text = seed.channel_id, seed.request_text
    known_env = await db.structured_memories(channel_id, kind=BACKEND_ENVIRONMENT)
    env = environment_of(_newest_domain(seed.turns), known_env)
    services = await db.structured_memories(channel_id, kind=BACKEND_SERVICE)
    resolved = _service_match(text, services)
    curl, response = _artifacts(seed)
    # Room rows come first, so the first of a name is the room's own.
    by_name: dict[str, Project] = {}
    for row in await db.structured_memories(channel_id, kind=BACKEND_PROJECT):
        by_name.setdefault(
            row.name,
            Project(
                name=row.name,
                repo_path=row.repo_path or "",
                docs_paths=tuple(row.docs_paths or ()),
                error_codes_doc=row.error_codes_doc or "",
            ),
        )
    projects = tuple(by_name.values())

    fields: dict[str, Any] = {
        "correlation_id": seed.hints.uuids[0] if seed.hints.uuids else None,
        "curl_artifact_id": curl,
        "response_artifact_id": response,
        "projects": projects,
    }
    if resolved is None:
        # Vague, or unnamed: the candidate set, for the agent to narrow by
        # reading — never a guess.
        return Placement(
            env=env, candidates=tuple(row.name for row in services), **fields
        )

    fields["service"] = resolved.name
    if env == "production":
        fields.update(
            cluster=resolved.prod.cluster,
            namespace=resolved.prod.namespace,
            app=resolved.prod.app,
        )
    elif env == "dev":
        fields.update(
            namespace=resolved.dev.namespace, pod_pattern=resolved.dev.pod_pattern
        )
    project = await db.structured_memory(
        channel_id, kind=BACKEND_PROJECT, key=resolved.project
    )
    if project is not None:
        fields.update(
            project=project.name,
            clone_path=project.repo_path or "",
            repo_path=project.repo_path or "",
            error_code_doc=project.error_codes_doc or "",
            stack=project.stack or "",
        )
    return Placement(env=env, **fields)
