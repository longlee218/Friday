"""The backend plugin: `backend.trace_problem`, `backend.answer_question` and
the backend pack kinds.

The one file the kernel knows about. `PLUGIN` is the plugin value; `register(api)`
is the single entrypoint the composition root calls — once per contribution
lifecycle (memory kinds are filled at one point in boot, task-type graphs at
another), so it declares everything each time and the host honours only the
calls its lifecycle serves.

This is the proof the split holds on the real task type (ticket 14): everything
under `plugins/backend/` imports `friday.sdk` and nothing else of ours, so the
kernel names none of it.
"""

from __future__ import annotations

from typing import Any

from friday.sdk import Plugin, TaskTypeSpec
from plugins.backend import answer_question
from plugins.backend.actions import ACTIONS
from plugins.backend.agents import AGENTS
from plugins.backend.evals import EVALS
from plugins.backend.graph import TASK_TYPE, build_backend_dag
from plugins.backend.memory import BACKEND_MEMORY_KINDS, DEPENDENCY_READERS
from plugins.backend.params import TraceProblemParams
from plugins.backend.placement import enrich
from plugins.backend.toolsets import TOOLSETS

__all__ = ["PLUGIN", "register"]


def register(api: Any) -> None:
    """Contribute the backend pack kinds, their reader routing, the four
    toolsets, the two agents, the `backend.trace_problem` and
    `backend.answer_question` actions and their task types, and the
    `backend.trace_problem` eval. The `graph`
    builder is deferred and closes over `api` (for `api.caps`), so it runs
    only once the boot capabilities exist."""
    for spec in BACKEND_MEMORY_KINDS:
        api.memory_kind(spec)
    for reader, kinds in DEPENDENCY_READERS.items():
        api.reader(reader, kinds)
    for toolset in TOOLSETS:
        api.toolset(toolset)
    for agent in AGENTS:
        api.agent(agent)
    for action in ACTIONS:
        api.action(action)
    for spec in EVALS:
        api.eval(spec)

    api.task_type(
        TaskTypeSpec(
            name=TASK_TYPE,
            params=TraceProblemParams,
            graph=lambda _deps: build_backend_dag(api),
            needs=frozenset({"source:loki", "backend.service"}),
        )
    )
    # No investigation past node 0: the shared simple graph, from the caps.
    api.task_type(
        TaskTypeSpec(
            name=answer_question.TASK_TYPE,
            params=answer_question.DocQuestionParams,
            graph=lambda _deps: api.caps.simple_dag(
                answer_question.TASK_TYPE, answer_question.DocQuestionParams
            ),
        )
    )


PLUGIN = Plugin(id="backend", register=register, enricher=enrich)
