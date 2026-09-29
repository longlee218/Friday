"""PROTOTYPE — throwaway. Ticket 02 "The merged Contract/Plan schema" on board
the-task-contract. Shapes to react to, not to ship. No behaviour.

The user's sketch (TypeScript):
  TaskContract = { objective, constraints[], acceptanceCriteria[],
                   allowedActions[], approvalPolicy[], budget{maxTime,maxCost,maxSteps} }
Durable-spine's Plan: { goal, budget, on_obstacle, steps[] }.
Decision Q3: these are ONE artifact. Decision Q1=c: hybrid — some fields are
type-level (authored once, inherited), some instance-level (per run).
Decision 01: the type-level half lands early; the instance-level half (objective,
model-proposed acceptance, dynamic steps) waits for durable-spine's Planner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


# ─────────────────────────────────────────────────────────────────────────────
# A single acceptance criterion — the new part, and the "definition of done".
# `check` says WHO verifies it (ticket 03): deterministic code, or the verifier
# agent (only once calibrated against operator marks).
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class Acceptance:
    name: str  # "grounded", "weighed", "root_cause"
    check: Literal["code", "agent"]  # deterministic vs model-judge (t03)
    description: str  # what "met" means, for the verifier


# ─────────────────────────────────────────────────────────────────────────────
# TYPE-LEVEL — operator-authored ONCE per task type; inherited by every run.
# **This is the part that lands early** (decision 01): a pure consolidation of
# config that is scattered today. No Planner needed.
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class Budget:
    max_steps: int  # today: MAX_READS = 12 (investigate.py)
    max_tokens: int  # today: extraction/agent token budget
    max_time_seconds: float  # today: node timeouts


@dataclass(frozen=True, slots=True)
class TaskContract:
    """The per-*type* template. Authored once for devops.api_issue."""

    task_type: str
    # today: the grounding gate ("a ref that resolves to nothing voids the
    # answer"), stated as an invariant rather than buried in _judged.
    constraints: tuple[str, ...] = ()
    # today: each tool's declared `needs` / the capabilities a run may reach.
    allowed_actions: tuple[str, ...] = ()
    # today: outbox approval — which outcomes wait for a person. `Reply` waits;
    # `Ask`/`Acknowledge` do not. A set of the actions that require approval.
    approval_policy: tuple[str, ...] = ("Reply",)
    budget: Budget = field(default_factory=lambda: Budget(12, 40_000, 90.0))
    # the per-type default "definition of done" — operator-authored (Q2).
    acceptance_template: tuple[Acceptance, ...] = ()


# ─────────────────────────────────────────────────────────────────────────────
# THE PER-RUN ARTIFACT — "one" (Q3). It carries the contract's fields (inherited)
# PLUS the instance-level parts. GatePlan validates THIS against its contract.
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class Step:
    """A plan step. FIXED today (the graph's nodes); a `Planner`'s output under
    durable-spine. Left thin here — its vocabulary is durable-spine's ticket."""

    id: str
    type: str  # e.g. "sub_agent", "read_source" (later)


@dataclass(frozen=True, slots=True)
class Plan:
    """The run's contract-and-plan, one artifact (Q3)."""

    contract: TaskContract  # inherited (type-level, lands now)
    # instance-level — the durable-spine half; deferred until the Planner:
    objective: str = ""  # this case's goal (== sketch.objective)
    acceptance: tuple[Acceptance, ...] = ()  # template + model-proposed-per-case
    steps: tuple[Step, ...] = ()  # fixed now; Planner-generated later
    on_obstacle: Literal["replan", "handover", "abort"] = "handover"


# ─────────────────────────────────────────────────────────────────────────────
# Mapping the user's TaskContract sketch onto this:
#   objective         -> Plan.objective            (instance)
#   constraints        -> TaskContract.constraints  (type)
#   acceptanceCriteria -> Acceptance[] (template on the type, resolved on Plan)
#   allowedActions     -> TaskContract.allowed_actions (type)
#   approvalPolicy     -> TaskContract.approval_policy (type)
#   budget             -> TaskContract.budget         (type)
#   (durable-spine)    -> Plan.steps / on_obstacle    (instance, later)
#
# WHAT LANDS NOW (decision 01): TaskContract (all of it, incl. acceptance_template)
#   + naming the deterministic grounding gate as `check="code"` Acceptances.
# WHAT WAITS: Plan.objective / model-proposed acceptance / Plan.steps /
#   on_obstacle — all need the Planner.
#
# OPEN for your reaction:
#  A. NAMING. Two names for one idea. Options: keep BOTH (`TaskContract` =
#     type template, `Plan` = per-run) as above; or one name for the per-run
#     artifact — `Plan` (carries contract) or `TaskContract` (carries steps).
#  B. Does the NOW artifact include `objective`/`steps` at all, or only the
#     TaskContract (type-level) + acceptance until the Planner exists? (If the
#     graph is still the "steps", `Plan.steps` is empty/ignored now.)
#  C. `approval_policy` as a set of action names ("Reply") — enough, or does it
#     need per-action rules (who approves, auto-approve when trusted)?
# ─────────────────────────────────────────────────────────────────────────────
