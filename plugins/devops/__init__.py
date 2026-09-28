"""The devops plugin: the `devops.api_issue` task type and its pack kinds.

The one file the kernel knows about. `PLUGIN` is the plugin value; `register(api)`
is the single entrypoint the composition root calls — once per contribution
lifecycle (memory kinds are filled at one point in boot, task-type graphs at
another), so it declares everything each time and the host honours only the
calls its lifecycle serves.

This is the proof the split holds on the real task type (ticket 14): everything
under `plugins/devops/` imports `friday.sdk` and nothing else of ours, so the
kernel names none of it.
"""

from __future__ import annotations

from typing import Any

from friday.sdk import Plugin, TaskTypeSpec
from plugins.devops.config import DevopsConfig
from plugins.devops.graph import (
    TASK_TYPE,
    build_devops_dag,
    build_log_sources,
    build_release_source,
)
from plugins.devops.graph.deps import ApiIssueDeps
from plugins.devops.memory import DEPENDENCY_READERS, DEVOPS_MEMORY_KINDS
from plugins.devops.params import ApiIssueParams

__all__ = ["PLUGIN", "register"]


def _enrich_deps(base: Any, api: Any) -> Any:
    """Enrich the kernel-built base `Deps` with `devops.api_issue`'s own handles
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
    """Contribute the devops pack kinds, their reader routing, and the
    `devops.api_issue` task type. The `graph`/`deps` builders are deferred and
    close over `api` (for `api.caps`), so they run only in the task-type
    lifecycle where the boot capabilities exist."""
    for spec in DEVOPS_MEMORY_KINDS:
        api.memory_kind(spec)
    for reader, kinds in DEPENDENCY_READERS.items():
        api.reader(reader, kinds)

    api.task_type(
        TaskTypeSpec(
            name=TASK_TYPE,
            params=ApiIssueParams,
            graph=lambda _deps: build_devops_dag(api),
            deps=lambda base: _enrich_deps(base, api),
            needs=frozenset({"source:loki", "devops.service"}),
        )
    )


PLUGIN = Plugin(id="devops", register=register, config=DevopsConfig)
