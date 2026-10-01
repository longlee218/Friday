"""The tool seam: how a plugin declares a tool without naming the harness.

A tool declaration is what pulls the agent SDK in — `friday/kernel/harness/harness.py`
is the one module that imports it. So a plugin declares a tool as a neutral
`ToolSpec` value (the function plus its options), and the harness turns each one
into the vendor's `Tool` when it builds the agent. The plugin names only this
module; the framework binding stays above the sdk line.

`ToolContext` is `Any` here: a plugin tool that reads no per-run context declares
nothing to subscript, and a tool that *does* want the typed run context is an
app-layer tool (the four skill tools) that imports `ToolContext` from the harness
where the real `RunContext` lives.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, overload

__all__ = ["RunContext", "ToolContext", "ToolSpec", "ToolsetSpec", "tool"]

#: A plugin tool that reads per-run state does so off `ctx.deps`; typed `Any`
#: here so a plugin never names the vendor's run-context type. The app-layer
#: tools that need the real typed context import `ToolContext` from the harness.
ToolContext = Any


@dataclass(frozen=True)
class ToolSpec:
    """One tool, as its plugin declares it: the function and the options the
    harness passes straight to the vendor's `Tool`. Built by `tool`, converted
    to a real `Tool` inside `Harness` — the one place the SDK is named."""

    fn: Callable[..., Any]
    options: dict[str, Any] = field(default_factory=dict)


@overload
def tool(func: Callable[..., Any]) -> ToolSpec: ...
@overload
def tool(
    func: None = None, **options: Any
) -> Callable[[Callable[..., Any]], ToolSpec]: ...
def tool(
    func: Callable[..., Any] | None = None, **options: Any
) -> ToolSpec | Callable[[Callable[..., Any]], ToolSpec]:
    """Declare a tool from a plain function, as a `ToolSpec`.

    A thin marker so tool modules name `tool` rather than the vendor. The
    harness reads the function's signature the same way the SDK does — a
    `ctx: ToolContext[...]` first parameter is the run context and is hidden
    from the model's schema; a google-style `Args:` docstring becomes each
    parameter's description. Anything passed as a keyword rides along in
    `options` to the vendor's `Tool`.

    Two call shapes, hence the `@overload`s above: bare (`tool(fn)`) returns
    the `ToolSpec` directly; with options (`@tool(description=...)`) returns
    a decorator. Type-only — same body either way.
    """

    def make(fn: Callable[..., Any]) -> ToolSpec:
        return ToolSpec(fn, options)

    return make(func) if func is not None else make


@dataclass(frozen=True, slots=True)
class RunContext:
    """What the core hands a toolset factory, once per run.

    `domain` is the domain's enricher output (e.g. `Placement`), `None` for a
    domain with no enricher. `evidence` is what the run has read — refs resolve
    against it. `mcp` maps each server the toolset declared to its `Reads`,
    narrowed to exactly the tools the toolset listed — the core fills it per
    toolset, so two toolsets in one run never see each other's reads.
    `reported_at` is when the reporter spoke (core Intake's seed), what a log
    window is measured back from; core data, so not on the domain type.
    `todos` is the agent's own checklist (`friday.sdk.todos.Todos`,
    build-the-spine ticket 24), `None` for a run that grants no `core.todo`.
    """

    task_id: int
    domain: Any
    evidence: Any
    mcp: Mapping[str, Any]
    reported_at: datetime
    todos: Any = None


@dataclass(frozen=True, slots=True)
class ToolsetSpec:
    """A named set of tools a plugin registers (`api.toolset`).

    `description` is for the Planner. `factory` builds the tools per run from
    a `RunContext`. `mcp` is the read allowlist per server, `{server: TOOLS}`
    — the core holds the raw servers and never hands a factory a tool outside
    it. `domain_type` is the type the factory expects in `run.domain`; `None`
    when it reads no domain.
    """

    name: str
    description: str
    factory: Callable[[RunContext], list[Any]]
    mcp: Mapping[str, frozenset[str]] = field(default_factory=dict)
    domain_type: type | None = None
