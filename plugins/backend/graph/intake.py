"""Intake — the DAG's node 0, now a thin door onto core Intake.

Core `intake()` (`friday/kernel/spine/intake.py`, reached through
`api.caps.intake` so this plugin imports no kernel) seeds from the task's
own text, runs the backend enricher (`plugins/backend/placement.py`), and
retrieves memory and skills. This node only stores the result as an envelope
and reads it back (`intake_of`) for acknowledge / diagnose / report, which
read `ctx.domain`. Build-the-spine ticket 07; the DAG path and this node go in
ticket 16.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from friday.sdk.intake import ArtifactRef, Hints, IntakeContext
from friday.sdk.workflow import DAGState, Node, envelope
from friday.sdk.workflow import Deps
from plugins.backend.graph.logs import _reported_at
from plugins.backend.placement import Placement, Project, enrich

__all__ = ["intake_node", "intake_of"]


def _listed(value: Any) -> Any:
    """`asdict` keeps a tuple a tuple; `DAGState.to_dict`'s round trip needs
    a list. Applied once, generically, because `IntakeContext` carries tuples
    at three levels."""
    if isinstance(value, tuple):
        return [_listed(v) for v in value]
    if isinstance(value, dict):
        return {k: _listed(v) for k, v in value.items()}
    return value


def intake_node(run_intake: Any) -> Node:
    """Build the node around core `intake` (`api.caps.intake`). No model:
    every field is a rule, a row, or a regex."""

    async def _intake(state: DAGState, deps: Deps) -> Any:
        context = await run_intake(
            deps.db,
            task_id=deps.task.id,
            channel_id=deps.task.conversation.channel_id,
            reported_at=_reported_at(deps.task).isoformat(),
            # The same function as `PLUGIN.enricher` — imported directly
            # because `plugins.backend` imports this module; ticket 14's pass
            # reads it off the plugin.
            enricher=enrich,
        )
        return envelope("ok", "", intake=_listed(asdict(context)))

    return Node("intake", _intake)


def intake_of(result: Any) -> IntakeContext:
    """Intake's envelope read back — the JSON round trip, undone: lists
    become tuples again, at every level that carries one."""
    data = result["intake"]
    domain = dict(data["domain"])
    for field_name in ("dbs", "candidates"):
        domain[field_name] = tuple(domain[field_name])
    domain["projects"] = tuple(
        Project(**{**p, "docs_paths": tuple(p["docs_paths"])})
        for p in domain.get("projects", ())
    )
    hints = data["hints"]
    return IntakeContext(
        request_text=data["request_text"],
        reported_at=data["reported_at"],
        hints=Hints(
            uuids=tuple(hints["uuids"]),
            artifacts=tuple(ArtifactRef(**a) for a in hints["artifacts"]),
        ),
        domain=Placement(**domain),
        memory=tuple(data["memory"]),
        skills=tuple(data["skills"]),
    )
