"""`core.planner`: the plan-shape eval, on synthetic cases (build-the-spine
ticket 11; board `domains-plug-in` ticket 12 §11).

Code-graded: each case is an intake context and the plan *shape* it should
come to — the terminal step (`draft | ask | hand_over`) and the agents in
order. The grant is not graded: the Planner writes none (ticket 22), every
agent step gets `full_grant`. `brief` and `goal` are not graded; scoring the plan's quality waits for real cases. The model is live
(a run costs money); what the Planner reads is fixed per case — its memory is
seeded into a throwaway store and its skills are the case's own folder — so a
changed number is the prompt's or the model's, not the operator's memory.

    evals/datasets/planner/<case>/case.md            the case
    evals/datasets/planner/<case>/skills/<name>/SKILL.md   optional

    ---
    action: backend.trace_problem
    memory: ["orders-api logs live in Loki"]         # optional
    expect:
      terminal: draft
      agents: [backend.diagnose]
    ---
    the request, as the reporter wrote it

Synthetic, so committed — unlike the triage set, which is real traffic.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from friday.kernel.spine.plan import AgentStep
from friday.kernel.spine.plan_gate import Frozen
from friday.sdk.eval import EvalCase, EvalSpec

__all__ = ["DATASET", "PLANNER_EVAL", "Expected", "Shape", "build_task", "load_cases"]

DATASET = Path(__file__).resolve().parents[3] / "evals" / "datasets" / "planner"
#: When every case says it was reported; nothing in a plan's shape reads it.
REPORTED_AT = "2026-09-29T10:00:00+07:00"
_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.DOTALL)


@dataclass(frozen=True, slots=True)
class Expected:
    terminal: str
    agents: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PlannerCase:
    action: str
    request: str
    memory: tuple[str, ...]
    skills: Path


@dataclass(frozen=True, slots=True)
class Shape:
    """What a plan came to, as graded. `failed` is the `planner_failed`
    reason when no plan passed GatePlan; the rest is then empty."""

    terminal: str | None
    agents: tuple[str, ...] = ()
    failed: str | None = None


def load_cases(root: Path = DATASET) -> list[EvalCase]:
    """Every `<case>/case.md` under `root`, sorted. A malformed case is
    refused with its path, never skipped."""
    return [_case(path) for path in sorted(root.glob("*/case.md"))]


def _case(path: Path) -> EvalCase:
    name = path.parent.name
    match = _FRONTMATTER.match(path.read_text(encoding="utf-8-sig"))
    if match is None:
        raise ValueError(f"{name}: no `---` frontmatter at the top")
    meta = yaml.safe_load(match.group(1)) or {}
    expect = meta.get("expect") or {}
    if (
        not meta.get("action")
        or not expect.get("terminal")
        or not match.group(2).strip()
    ):
        raise ValueError(f"{name}: needs `action`, `expect.terminal` and a request")
    return EvalCase(
        name=name,
        inputs=PlannerCase(
            action=meta["action"],
            request=match.group(2).strip(),
            memory=tuple(meta.get("memory") or ()),
            skills=path.parent / "skills",
        ),
        expected=Expected(
            terminal=expect["terminal"],
            agents=tuple(expect.get("agents") or ()),
        ),
    )


def shape_of(got: Any) -> Shape:
    """A `Frozen` plan's shape, or a `PlannerFailed`'s reason."""
    if not isinstance(got, Frozen):
        return Shape(terminal=None, failed=got.reason)
    return Shape(
        terminal=got.plan.steps[-1].type,
        agents=tuple(s.agent for s in got.plan.steps if isinstance(s, AgentStep)),
    )


def build_task(
    config: Any, registry: Any, *, model: Any = None
) -> Callable[[EvalCase], Awaitable[Shape]]:
    """The task: the real Planner on `config`'s `strong` tier, the registered
    actions, agents and toolsets, and the case's fixed memory and skills.
    `model` is a scripted transport, for the suite."""
    from friday.kernel.domain.state import FridayState
    from friday.kernel.harness.skills import SkillLibrary
    from friday.kernel.spine.intake import hints_of
    from friday.kernel.spine.planner import PLANNER, Planning, plan
    from friday.kernel.toolsets import core_toolsets
    from friday.sdk.intake import IntakeContext
    from friday.store.db import Database

    async def run(case: EvalCase) -> Shape:
        c: PlannerCase = case.inputs
        state = FridayState(channel_id=f"eval-{case.name}", agent=PLANNER.name)
        db = await Database.connect(":memory:", create=True)
        try:
            for text in c.memory:
                await db.memory_add(state, text)
            library = SkillLibrary(c.skills).load()
            toolsets = {
                **registry.toolsets(),
                **{t.name: t for t in core_toolsets(db=db, skills=library)},
            }
            got = await plan(
                Planning(
                    config=config.agent(PLANNER),
                    task_id=0,
                    action=registry.actions()[c.action],
                    intake=IntakeContext(
                        request_text=c.request,
                        reported_at=REPORTED_AT,
                        hints=hints_of(c.request),
                        memory=c.memory,
                        skills=tuple(library.catalogue()),
                    ),
                    agents=registry.agents(),
                    toolsets=toolsets,
                    state=state,
                    model=model,
                )
            )
        finally:
            await db.close()
        return shape_of(got)

    return run


def _terminal(case: EvalCase, shape: Shape) -> bool:
    return shape.terminal == case.expected.terminal


def _agents(case: EvalCase, shape: Shape) -> bool:
    return shape.failed is None and shape.agents == case.expected.agents


CHECKS = {"terminal": _terminal, "agents": _agents}


def report(results: Sequence[tuple[EvalCase, Shape]]) -> str:
    n = len(results)
    passed = [all(check(c, s) for check in CHECKS.values()) for c, s in results]
    lines = [f"{n} cases, whole shape right: {sum(passed)}/{n}", ""]
    for label, check in CHECKS.items():
        lines.append(f"  {label}: {sum(check(c, s) for c, s in results)}/{n}")
    failed = sum(s.failed is not None for _, s in results)
    lines.append(f"  planner_failed: {failed}/{n}")
    wrong = [(c, s) for (c, s), ok in zip(results, passed) if not ok]
    lines += ["", f"wrong: {len(wrong)}/{n}"]
    for c, s in wrong:
        got = s.failed or f"{s.terminal} via {list(s.agents)}"
        e = c.expected
        lines.append(
            f"  {c.name}: got {got}; expected {e.terminal} via {list(e.agents)}"
        )
    return "\n".join(lines)


PLANNER_EVAL = EvalSpec(
    name="core.planner",
    description="The Planner's plan shape for each synthetic case in "
    "evals/datasets/planner/: terminal step and agents.",
    cases=load_cases,
    checks=CHECKS,
    report=report,
)
