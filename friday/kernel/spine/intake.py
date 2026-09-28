"""Core Intake: seed → the domain's one enricher → retrieve. No model, no
network — every field is a regex, a row, or the enricher's own DB read, so it
re-runs every reply pass cheaply and its identity diff stays stable (board
`domains-plug-in` ticket 04; build-the-spine ticket 07).

Live through the backend DAG's intake node until ticket 14 puts it on the
spine pass.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

from friday.sdk.intake import ArtifactRef, Hints, IntakeContext, IntakeSeed

__all__ = ["MEMORY_CAP", "SKILLS_CAP", "hints_of", "intake"]

_UUID = re.compile(r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}")
#: `[artifact <id>: <description>]`, as `friday/store/_common.py`'s
#: `_ARTIFACT_REF` writes it.
_ARTIFACT_REF = re.compile(r"\[artifact ([0-9a-f]+): ([^\]]*)\]")

#: Count bounds, not a token estimator.
MEMORY_CAP = 20
SKILLS_CAP = 10

Enricher = Callable[[IntakeSeed, Any], Awaitable[Any]]


def hints_of(text: str) -> Hints:
    """Every uuid and every artifact ref in `text`, in order."""
    return Hints(
        uuids=tuple(_UUID.findall(text)),
        artifacts=tuple(ArtifactRef(i, d) for i, d in _ARTIFACT_REF.findall(text)),
    )


async def intake(
    db: Any,
    *,
    task_id: int,
    channel_id: str,
    reported_at: str,
    enricher: Enricher | None,
) -> IntakeContext:
    """The context every agent in the run receives. `enricher` is the task's
    domain's (`Plugin.enricher`); `None` for a domain with none."""
    turns = tuple(await db.original_turns_for(task_id))
    request_text = "\n".join(turns)
    seed = IntakeSeed(channel_id, request_text, reported_at, hints_of(request_text), turns)
    domain = await enricher(seed, db) if enricher is not None else None
    keys = domain.retrieval_keys() if domain is not None else {}
    records = await db.case_memories(channel_id, keys, request_text)
    return IntakeContext(
        request_text=request_text,
        reported_at=reported_at,
        hints=seed.hints,
        domain=domain,
        memory=tuple(r.text for r in records if r.kind != "skill")[:MEMORY_CAP],
        skills=tuple(r.text for r in records if r.kind == "skill")[:SKILLS_CAP],
    )
