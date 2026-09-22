"""Node 1: which environment, which service, and where it runs.

**The environment comes from the domain, by a table the operator wrote.**
Not from the reporter, who says "dev" meaning the dev *app* against
production, and not from a model, which would be asked to reproduce a lookup
it cannot be wrong about cheaply. Three outcomes and no fourth: `dev`,
`production`, `external`.

**This amends D1** ("by rule, in code"), on the operator's call of
2026-09-21. Two reasons, and the second is the one that settles it. Friday is
meant to serve more rooms than one company's, and a module naming
`aperogroup.ai` is an installation compiled into the system. And the rule was
already wrong about this company: the 2026-09-18 survey found
`api-mobile-spec-reviewer.aperogroup.ai` served from `dev` with no `.dev` in
it, and `payment-service` on two domains resolving to different endpoints. A
longest-suffix table holds the rule and its exceptions with no branch for
either — `aperogroup.ai → production`, `dev.aperogroup.ai → dev`, and the
exception is one more row that wins by being longer.

**Where it runs comes from rows the operator wrote** (D3). Domain → `route`
→ `service`, and the service row carries the Loki labels for production and
the pod pattern for dev. A missing row is a hand-over, not a guess: guessing
which pod serves a domain is how a diagnosis gets built from another
product's logs (finding G).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

from friday.sdk.workflow import Deps as DAGDeps, DAGState, Node, envelope
from friday.domain.actions import HandOver
from friday.domain.models import MemoryKind
from friday.sources import Placement

__all__ = [
    "domain_of",
    "environment_of",
    "path_of",
    "resolve_node",
    "resolved",
]

log = logging.getLogger(__name__)

_URL = re.compile(r"https?://(?P<host>[\w.-]+)")


def resolved(result: Any) -> tuple[Placement, dict[str, Any] | None]:
    """Read node 1's envelope back: where to look, and the project row.

    A pair of plain values rather than an object, because what goes into the
    envelope is JSON — `DAGState.to_dict` replaces anything that does not
    survive a round trip with a marker, and a node marked unstorable runs
    again after a restart. Cheap here, but the same envelope is what the
    board renders, and a marker renders as nothing at all.
    """
    return Placement(**result["placement"]), result.get("project")


def domain_of(curl: str | None) -> str | None:
    """The host of the request in a curl, or `None`.

    The first URL in the text, not the last: a curl carries one request, and
    anything else shaped like a URL in it is a header value or a comment.
    """
    if not curl:
        return None
    found = _URL.search(curl)
    return found.group("host").lower() if found else None


def path_of(curl: str | None) -> str | None:
    """The request's path — what the log line carries when the reporter
    pasted no correlationId (D2: a curl, *or* an endpoint plus one identifier
    the log line carries).

    Beside `domain_of` because both read the same URL, and two patterns that
    have to agree on what a URL is are two patterns that one day do not.
    """
    if not curl:
        return None
    found = _URL.search(curl)
    if not found:
        return None
    rest = curl[found.end():]
    path = rest.split()[0].split("?")[0].strip("\"'") if rest.split() else ""
    return path or None


def environment_of(domain: str | None, known: Sequence[Any]) -> str:
    """Which environment this domain is, by the longest `environment` row
    that matches it. `external` when none does.

    Longest wins, and that is the whole mechanism: `aperogroup.ai →
    production` and `dev.aperogroup.ai → dev` express the convention, and a
    single row for one host that breaks it wins over both without any code
    knowing it is an exception.

    `external` covers a third-party service and a proxy of ours the table
    does not know yet alike, because from here they are the same thing: a
    domain nobody wrote down. Which of the two it is is the operator's call.
    """
    if not domain:
        return "external"
    matched = [
        row for row in known
        if domain == row.suffix or domain.endswith(f".{row.suffix}")
    ]
    if not matched:
        return "external"
    return max(matched, key=lambda row: len(row.suffix)).env


def resolve_node(*, timeout_seconds: float | None = None) -> Node:
    """Build node 1. No model: every decision here is a rule or a row."""

    async def _resolve(state: DAGState, deps: DAGDeps) -> Any:
        params = state["prepare"]
        channel_id = deps.task.conversation.channel_id
        domain = domain_of(getattr(params, "curl", None))

        if domain is None:
            # D2: "Without a curl there is no domain". A report is findable
            # without one — `_traceable` lets an endpoint plus an identifier
            # through, and ticket 01 made that the point of the type — but
            # findable is not the same as routable, and the routing table is
            # keyed on the domain. Naming a service another way is still not
            # a thing a task can do, so this is a hand-over that says which
            # of the two is missing, rather than the `external` one, which
            # would blame a domain nobody wrote.
            return HandOver(
                "nothing here carries the URL that was called, so I cannot "
                "tell which service or environment this is about. An "
                "endpoint name or a correlationId does not say which host "
                "it was called on."
            )

        known = await deps.db.structured_memories(
            channel_id, kind=MemoryKind.ENVIRONMENT
        )
        if not known:
            # Nothing has been written down about any domain. Saying
            # "external" here would be a claim — that this domain is not ours
            # — made by a room that knows nothing about any domain at all.
            return HandOver(
                f"nothing here says which domains are ours, so I cannot tell "
                f"what {domain} is. One `environment` row per domain suffix "
                f"— `aperogroup.ai` is production, `dev.aperogroup.ai` is dev "
                f"— is what this reads."
            )

        env = environment_of(domain, known)

        if env == "external":
            return HandOver(
                f"the request goes to {domain}, which no `environment` row "
                "matches — so it is not one of ours and I have not looked at "
                "anything. If it is a proxy of ours, the table does not know "
                "it yet."
            )

        route = await deps.db.structured_memory(
            channel_id, kind=MemoryKind.ROUTE, key=domain
        )
        if route is None:
            return HandOver(
                f"no route row for {domain}. Nothing says which service that "
                "domain is, and guessing one reads another product's logs."
            )
        if route.env != env:
            return HandOver(
                f"the route row for {domain} says {route.env}, and the domain "
                f"says {env}. One of the two is wrong and I will not pick."
            )

        service = await deps.db.structured_memory(
            channel_id, kind=MemoryKind.SERVICE, key=route.service
        )
        if service is None:
            return HandOver(
                f"{domain} routes to the service {route.service!r}, which has "
                "no row — so nothing says which pod or Loki app it is."
            )

        placement = (
            Placement(
                env=env,
                service=route.service,
                cluster=service.prod.cluster,
                namespace=service.prod.namespace,
                app=service.prod.app,
            )
            if env == "production"
            else Placement(
                env=env,
                service=route.service,
                namespace=service.dev.namespace,
                pod_pattern=service.dev.pod_pattern,
            )
        )
        project = await deps.db.structured_memory(
            channel_id, kind=MemoryKind.PROJECT, key=service.project
        )
        if project is None:
            log.info(
                "task %s: no project row for %s — the code node will say so",
                deps.task.id, service.project,
            )
        return envelope(
            "ok",
            "",
            domain=domain,
            placement=asdict(placement),
            project=None if project is None else asdict(project),
        )

    return Node("resolve", _resolve, timeout_seconds=timeout_seconds)
