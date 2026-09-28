"""The action declaration: what a plugin registers for one kind of work.

Pure data, no I/O. An `Action` joins what triage reads (`Recognition`) to what
bounds a run (`ActionContract`); only the contract travels with a plan, so
rewording recognition never changes a plan's hash (board `domains-plug-in`,
tickets 01 and 02). Registered beside the old `TaskTypeSpec` until
build-the-spine ticket 16 deletes that.

Not to be confused with `friday.sdk.actions.Action`, the `Ask | Reply |
HandOver` union a graph returns — that one becomes `Outcome` in ticket 06.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

__all__ = ["Action", "ActionContract", "Limits", "Recognition"]


@dataclass(frozen=True, slots=True)
class Recognition:
    """How triage tells this action from the others.

    `means` is one sentence: what a message of this kind asks for. `pick_when`
    lists the signals that point here. `not_when` pairs *(signal, other
    action's name)* — it decides between two actions, declared on one side
    only. `examples` are message texts whose label is this action.
    """

    means: str
    pick_when: tuple[str, ...]
    not_when: tuple[tuple[str, str], ...] = ()
    examples: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Limits:
    """How far one task may go: replans and plan steps."""

    max_replans: int
    max_steps: int


@dataclass(frozen=True, slots=True)
class ActionContract:
    """The ceiling the plugin author writes in code for this kind of work.

    The Planner picks a subset per run and never adds; GatePlan refuses
    anything outside. `allowed_step_types` is drawn from `agent`, `ask`,
    `hand_over`, `draft`. Neither `config.yaml`, the board nor a model can
    widen any of these lists.
    """

    allowed_step_types: frozenset[str]
    allowed_agents: frozenset[str]
    allowed_toolsets: frozenset[str]
    constraints: tuple[str, ...]
    approval_policy: str
    acceptance_template: str
    limits: Limits


@dataclass(frozen=True, slots=True)
class Action:
    """One kind of work a plugin offers.

    `acknowledge` (given the intake context) returns the one message sent
    before planning, or `None` for none. `planning` is optional prose for the
    Planner. Both sit outside the contract, like `recognition`.
    """

    name: str
    recognition: Recognition
    contract: ActionContract
    acknowledge: Callable[[Any], str | None] | None = None
    planning: str | None = None
