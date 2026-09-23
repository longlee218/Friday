"""Every task type, registering itself — the plugin move, one step early.

Ticket 11: instead of the router naming `api_issue` and looping `PARAMS`, each
task type contributes a `TaskTypeSpec` here, and `register_all` fills the
registry at boot. The router, extraction and triage then read the registry and
name no type — which is what lets ticket 14 lift `api_issue` out to a plugin with
no change to the core.

`register_all` builds each type's graph at boot from a `BootContext` (config plus
the composition root's `record`/`spent`/`servers`). The graph is stored behind a
`Deps`-taking factory it currently ignores (ticket 13 makes it real). `api_issue`
owns everything specific to it — its investigation graph, its diagnose harness,
its log/release sources — so none of that lives in the router any more.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from friday.dag import registry
from friday.domain.models import (
    AccessRequestParams,
    ApiIssueParams,
    DocQuestionParams,
)
from friday.sdk.plugin import TaskTypeSpec
from friday.sdk.workflow import DAG

__all__ = ["BootContext", "register_all"]


@dataclass(frozen=True)
class BootContext:
    """What building a task type's graph needs that the registry cannot hold: the
    configuration, the recording sink and budget ledger every model node shares,
    the tool servers a run opened, and the skill library. The composition root
    has these; the registry is filled from them once, here."""

    config: Any
    record: Any = None
    spent: Any = None
    servers: dict[str, Any] = field(default_factory=dict)
    skills: Any = None


def _graph_of(dag: DAG):
    """Store a built graph behind the `Deps`-taking factory `TaskTypeSpec.graph`
    expects. Ticket 11 builds at boot and ignores the `Deps`; ticket 13's typed
    per-run `Deps` factory replaces this."""
    return lambda _deps: dag


def register_all(ctx: BootContext) -> None:
    """Fill the registry with every task type this build knows. Clears first, so
    a second call (a restart in one process, a test) states the same intention
    rather than colliding with the last."""
    registry.clear()
    registry.SERVERS.update(ctx.servers)
    _register_api_issue(ctx)
    _register_simple(ctx, name="access_request", params=AccessRequestParams)
    _register_simple(ctx, name="doc_question", params=DocQuestionParams)


def _register_simple(ctx: BootContext, *, name: str, params: type) -> None:
    """A type with no investigation past node 0: prepare, then ask for what is
    missing or hand over (D1). The one-node graph every type but `api_issue`
    uses."""
    from friday.dag.router import build_simple_dag

    dag = build_simple_dag(
        name,
        params,
        budget_tokens=ctx.config.context.extraction_budget_tokens,
        extractor=ctx.config.agents.get("extractor"),
    )
    registry.register_task_type(TaskTypeSpec(name=name, params=params, graph=_graph_of(dag)))


def _register_api_issue(ctx: BootContext) -> None:
    """The one type with an investigation past node 0 (ticket 00), and the only
    one that reaches outside the process — so it owns its diagnose harness and
    its log/release sources, and the run handles they need travel in
    `DEPS_EXTRA`. None of this names `api_issue` in the router any more."""
    from friday.dag.api_issue import (
        TASK_TYPE,
        build_api_issue_dag,
        build_diagnose_harness,
        build_log_sources,
        build_release_source,
    )
    from friday.outbox import DEFAULT_APPROVER, DEFAULT_SENDER

    settings = getattr(ctx.config, "api_issue", None)
    dag = build_api_issue_dag(
        extractor=ctx.config.agents.get("extractor"),
        diagnose=ctx.config.agents.get("diagnose"),
        diagnose_harness=build_diagnose_harness(
            ctx.config, record=ctx.record, spent=ctx.spent
        ),
        # A factory, because under v3.3 the tools carry this run's placement and
        # numbering — one harness built at boot would read the previous case's
        # service. Built here, where `record`/`spent` are.
        make_diagnose_harness=(
            (lambda *, tools: build_diagnose_harness(
                ctx.config, record=ctx.record, spent=ctx.spent, tools=tools
            ))
            if settings is not None and settings.diagnose_reads
            else None
        ),
        budget_tokens=ctx.config.context.extraction_budget_tokens,
        reports_dir=(None if settings is None else Path(settings.reports_dir)),
    )

    # Two identities, and which one a row carries decides who reads it: `sender`
    # posts into the reporter's channel as the watched account; `approver`
    # direct-messages the operator. The graph queues rows for both mid-run,
    # before any action reaches the pool.
    extra: dict[str, Any] = {"sender": DEFAULT_SENDER, "approver": DEFAULT_APPROVER}
    sources = build_log_sources(ctx.config, dict(ctx.servers))
    if sources:
        extra["log_sources"] = sources
    release = build_release_source(ctx.config, dict(ctx.servers))
    if release is not None:
        extra["release_source"] = release

    clock = getattr(settings, "timeout_seconds", None)
    registry.register_task_type(
        TaskTypeSpec(name=TASK_TYPE, params=ApiIssueParams, graph=_graph_of(dag)),
        deps_extra=extra,
        budget=None if clock is None else float(clock),
    )
