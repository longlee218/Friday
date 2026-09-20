"""Node 1: which environment, which service, and where it runs.

**The environment comes from the domain, by rule, in code** (D1). Not from
the reporter, who says "dev" meaning the dev *app* against production, and
not from a model, which would be asked to reproduce a lookup table it cannot
be wrong about cheaply. Three outcomes and no fourth: `dev`, `production`,
`external`.

**Where it runs comes from rows the operator wrote** (D3). Domain → `route`
→ `service`, and the service row carries the Loki labels for production and
the pod pattern for dev. A missing row is a hand-over, not a guess: guessing
which pod serves a domain is how a diagnosis gets built from another
product's logs (finding G).
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from typing import Any

from friday.dag.engine import DAGDeps, DAGState, Node, envelope
from friday.domain.actions import HandOver
from friday.domain.models import MemoryKind

__all__ = [
    "Placement",
    "domain_of",
    "environment_of",
    "path_of",
    "resolve_node",
    "resolved",
]

log = logging.getLogger(__name__)

#: Ours, and how to tell the two environments apart. In code because D1 says
#: so, and narrow on purpose: the third outcome is `external`, which ends the
#: graph rather than guessing, so a domain missing from this tuple costs a
#: hand-over naming it and never a search of the wrong cluster.
_OURS = ("aperogroup.ai", "apero.vn")

#: There is no staging anywhere (D1), so this is the whole of the dev rule.
_DEV_LABEL = "dev"

_URL = re.compile(r"https?://(?P<host>[\w.-]+)")


@dataclass(frozen=True, slots=True)
class Placement:
    """Where one service's logs are, for the environment in hand.

    Flattened out of `ServiceData`'s two halves on purpose: every reader past
    this node wants one place, and a node that has to remember which half to
    read is a node that one day reads the other.
    """

    env: str
    service: str
    #: Production: the Loki labels. Dev: empty.
    cluster: str = ""
    namespace: str = ""
    app: str = ""
    #: Dev: the pod name pattern to grep for. Production: empty.
    pod_pattern: str = ""


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


def environment_of(domain: str | None) -> str:
    """D1's rule. `external` for anything that is not ours — including an
    unknown domain that may well be a proxy of ours the table does not know
    yet, which is the operator's call and not this function's."""
    if not domain:
        return "external"
    if not any(domain == own or domain.endswith(f".{own}") for own in _OURS):
        return "external"
    return _DEV_LABEL if f".{_DEV_LABEL}." in f".{domain}" else "production"


def resolve_node(*, timeout_seconds: float | None = None) -> Node:
    """Build node 1. No model: every decision here is a rule or a row."""

    async def _resolve(state: DAGState, deps: DAGDeps) -> Any:
        params = state["prepare"]
        domain = domain_of(getattr(params, "curl", None))

        if domain is None:
            # D2: "Without a curl there is no domain". A report is findable
            # on a correlationId alone — `_traceable` lets one through — but
            # findable is not the same as routable, and the routing table is
            # keyed on the domain. Ticket 01 is where a task learns to name
            # its service another way; until then this is a hand-over that
            # says which of the two is missing, rather than the `external`
            # one, which would blame a domain nobody wrote.
            return HandOver(
                "nothing here carries the URL that was called, so I cannot "
                "tell which service or environment this is about. The "
                "correlationId alone does not say."
            )

        env = environment_of(domain)

        if env == "external":
            return HandOver(
                f"the request goes to {domain or 'a domain nobody named'}, "
                "which is not one of ours — I have not looked at anything. "
                "If it is a proxy of ours, the routing table does not know it "
                "yet."
            )

        channel_id = deps.task.conversation.channel_id
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
