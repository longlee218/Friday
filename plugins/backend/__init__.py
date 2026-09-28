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
from plugins.backend.config import BackendConfig
from plugins.backend.graph import (
    TASK_TYPE,
    build_backend_dag,
    build_log_sources,
    build_release_source,
)
from plugins.backend.graph.deps import ApiIssueDeps
from plugins.backend.memory import DEPENDENCY_READERS, BACKEND_MEMORY_KINDS
from plugins.backend.params import ApiIssueParams

__all__ = ["PLUGIN", "register"]


def _enrich_deps(base: Any, api: Any) -> Any:
    """Enrich the kernel-built base `Deps` with `backend.trace_problem`'s own handles
    — the typed per-run `Deps` (§5.2). The log/release sources are built from the
    run's servers and the plugin's config; `sender`/`approver` are the two
    identities a mid-run row is queued as, from the composition root's caps."""
    return ApiIssueDeps(
        task=base.task,
        db=base.db,
        servers=base.servers,
        extra=base.extra,
        answers=base.answers,
        sender=api.caps.sender,
        approver=api.caps.approver,
        log_sources=build_log_sources(api.config, base.servers),
        release_source=build_release_source(api.config, base.servers),
        container_roots=api.config.container_roots,
        not_ours=api.config.not_ours,
    )


def register(api: Any) -> None:
    """Contribute the backend pack kinds, their reader routing, and the
    `backend.trace_problem` and `backend.answer_question` task types. The `graph`/`deps` builders are deferred and
    close over `api` (for `api.caps`), so they run only in the task-type
    lifecycle where the boot capabilities exist."""
    for spec in BACKEND_MEMORY_KINDS:
        api.memory_kind(spec)
    for reader, kinds in DEPENDENCY_READERS.items():
        api.reader(reader, kinds)

    api.task_type(
        TaskTypeSpec(
            name=TASK_TYPE,
            params=ApiIssueParams,
            graph=lambda _deps: build_backend_dag(api),
            deps=lambda base: _enrich_deps(base, api),
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


PLUGIN = Plugin(id="backend", register=register, config=BackendConfig)
