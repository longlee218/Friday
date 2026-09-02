"""Node 0, shared by every graph.

D2: "extract and validate" is the entrypoint of every graph — see ticket 03
— not a gate in front of one, and not a copy per graph either. Every graph
needs exactly the same shape here: read everything the reporter has said,
fill in the parameters, write them back, check them. One function builds it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict

from friday.dag import DAGDeps, DAGState, Node
from friday.domain.actions import Action, Ask
from friday.domain.models import Params
from friday.workflows import prepare as _prepare_params

__all__ = ["prepare_node", "prepared_ok"]


def prepare_node(
    task_type: str,
    params_cls: type[Params],
    *,
    on_ready: Callable[[Params], Params | Action] | None = None,
) -> Node:
    """Node 0: everything the reporter has said, filled in and checked.

    Runs on every pass — there may be a new message since the last one — and
    is never part of the checkpoint (`_run_dag` excludes it before saving);
    only its *output* decides whether the rest of what was checkpointed is
    still worth keeping.

    `on_ready` is what a graph with nothing past node 0 uses to turn a
    complete, valid set of parameters into its own answer. A type with an
    investigation past this node leaves it `None` and reads `state["prepare"]`
    itself instead.
    """

    async def _prepare(state: DAGState, deps: DAGDeps) -> Params | Action:
        known = params_cls(**deps.task.params)
        text = await deps.db.original_text_for(deps.task.id)
        filled, problem = await _prepare_params(task_type, known, text=text)

        merged = {**deps.task.params, **asdict(filled)}
        if merged != deps.task.params:
            await deps.db.set_task_params(deps.task.id, merged)

        if problem is not None:
            return problem
        return on_ready(filled) if on_ready else filled

    return Node("prepare", _prepare)


def prepared_ok(state: DAGState) -> bool:
    """Whether `prepare` cleared the report to continue past node 0."""
    return not isinstance(state["prepare"], Ask)
