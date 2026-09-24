"""Every task type, registered — the in-core simple ones here, the rest by plugin.

Ticket 11 gave each task type a `TaskTypeSpec` and had `register_all` fill the
registry at boot; ticket 14 lifted the one real investigation type
(`devops.api_issue`) out to `plugins/devops/`, and ticket 15 lifted the
`doc_question` persona out to `plugins/docs/`, leaving only `access_request`
here. `register_all` registers it and then loads every configured plugin,
handing each a `TaskTypeAPI` built from the
`BootContext` — which is also the **caps** a plugin's graph builder reaches for:
its `prepare_node` (node 0, so a plugin imports none of the kernel's graph
machinery), its `make_harness` (a model node), the run's tool servers, and the
`sender`/`approver` identities a queued row uses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from friday.kernel.dag import registry
from friday.kernel.domain.models import AccessRequestParams
from friday.kernel.outbox import DEFAULT_APPROVER, DEFAULT_SENDER
from friday.sdk.plugin import TaskTypeSpec

__all__ = ["BootContext", "register_all"]


@dataclass(frozen=True)
class BootContext:
    """What building a task type's graph needs that the registry cannot hold —
    and the capabilities a plugin's graph builder reaches through `api.caps`.

    `config` is the whole application `Config` (a plugin reads the shared
    `extractor` agent, its own model agent, and the extraction budget off it);
    `record`/`spent` are the sink and ledger every model node shares; `servers`
    are the tool servers a run opened; `skills` is the skill library. `sender`/
    `approver` are the two identities a mid-run row is queued as. `prepare_node`
    and `make_harness` are the kernel-side builders a plugin calls rather than
    imports.
    """

    config: Any
    record: Any = None
    spent: Any = None
    servers: dict[str, Any] = field(default_factory=dict)
    skills: Any = None
    sender: str = DEFAULT_SENDER
    approver: str = DEFAULT_APPROVER

    def prepare_node(self, *args: Any, **kwargs: Any) -> Any:
        """Node 0 — the extractor node every task type runs. Exposed here so a
        plugin builds node 0 without importing `friday.kernel.dag.prepare`."""
        from friday.kernel.dag.prepare import prepare_node

        return prepare_node(*args, **kwargs)

    def simple_dag(self, name: str, params: type) -> Any:
        """The one-node graph a type with no investigation past node 0 uses —
        prepare, then ask for what is missing or hand over (D1). Exposed here so
        a *simple* plugin (a second persona like `docs`, its whole graph node 0)
        builds it without importing `friday.kernel.dag.router`, the same way an
        investigation plugin reaches `prepare_node`/`make_harness`. Reads the
        shared `extractor` agent and node-0 budget off the config it carries."""
        from friday.kernel.dag.router import build_simple_dag

        return build_simple_dag(
            name,
            params,
            budget_tokens=self.config.context.extraction_budget_tokens,
            extractor=self.config.agents.get("extractor"),
        )

    def make_harness(
        self,
        *,
        agent: Any,
        instructions: str,
        answers: type | None = None,
        tools: list | None = None,
    ) -> Any:
        """A model harness built from a configured agent, or `None` when the
        agent is not configured — a fresh install, and every test that does not
        set one up. Built here, where `record`/`spent` live, so a plugin's model
        calls are recorded and counted against the same budget as everybody's,
        without the plugin importing the `Harness` class."""
        if agent is None:
            return None
        from friday.kernel.harness.harness import Harness

        return Harness(
            config=agent,
            instructions=instructions,
            answers=answers,
            tools=tools,
            record=self.record,
            spent=self.spent,
        )


def register_all(ctx: BootContext) -> None:
    """Fill the registry with every task type this build knows: the in-core
    simple types, then every configured plugin's. Clears first, so a second call
    (a restart in one process, a test) states the same intention rather than
    colliding with the last."""
    from friday.kernel.plugin_host import TaskTypeAPI, configured_plugins

    registry.clear()
    registry.SERVERS.update(ctx.servers)
    _register_simple(ctx, name="access_request", params=AccessRequestParams)
    for plugin, cfg in configured_plugins(ctx.config):
        plugin.register(TaskTypeAPI(caps=ctx, config=cfg))


def _register_simple(ctx: BootContext, *, name: str, params: type) -> None:
    """A type with no investigation past node 0: prepare, then ask for what is
    missing or hand over (D1). The one-node graph every simple type uses."""
    from friday.kernel.dag.router import build_simple_dag

    dag = build_simple_dag(
        name,
        params,
        budget_tokens=ctx.config.context.extraction_budget_tokens,
        extractor=ctx.config.agents.get("extractor"),
    )
    registry.register_task_type(TaskTypeSpec(name=name, params=params), dag=dag)
