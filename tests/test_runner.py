"""build-the-spine ticket 12 — the runner and `step_results`.

The runner walks a frozen plan: a step with a stored result is skipped and its
readers get that result; else the step runs and its result is stored at
`(task_id, step_key)`. A failing step is tried `STEP_ATTEMPTS` times, then
`HandOver step_failed`. `Ask`/`HandOver`/`Retriage` stop the plan; `Replan`
asks the Planner for the next version until `max_replans` is spent (board
`domains-plug-in`, ticket 13 as amended by 16 and 17).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from friday.kernel.spine.plan import (
    AgentStep,
    AskStep,
    DraftStep,
    HandOverStep,
    Plan,
)
from friday.kernel.spine.plan_gate import Frozen, gate_plan
from friday.kernel.spine.runner import STEP_ATTEMPTS, Resume, Steps, run_plan
from friday.sdk.action import ActionContract, Limits
from friday.sdk.actions import Ask, HandOver, Replan, Reply, Retriage
from friday.sdk.agent import AgentSpec, Budget
from friday.sdk.evidence import Evidence

ROOT = Path(__file__).resolve().parent.parent
PLACEMENT = ("prod", "onboarding")


@dataclass(frozen=True)
class Found:
    summary: str
    refs: list[str] = field(default_factory=list)


AGENTS = {
    "backend.diagnose": AgentSpec(
        name="backend.diagnose",
        description="finds the cause",
        instructions="find it",
        result=Found,
        tier="flash",
        toolsets=("backend.logs",),
        budget=Budget(max_turns=5, tokens=1000),
        temperature=0.0,
    ),
}


def _contract(max_replans: int = 2) -> ActionContract:
    return ActionContract(
        allowed_step_types=frozenset({"agent", "ask", "hand_over", "draft"}),
        allowed_agents=frozenset({"backend.diagnose"}),
        allowed_toolsets=frozenset({"backend.logs"}),
        constraints=(),
        approval_policy="always",
        acceptance_template="",
        limits=Limits(max_replans=max_replans, max_steps=5),
    )


def _frozen(*steps, version: int = 1, max_replans: int = 2) -> Frozen:
    got = gate_plan(
        Plan(
            task_id=7,
            action="backend.trace_problem",
            plan_version=version,
            replaces=None,
            contract=_contract(max_replans),
            goal="find the 400",
            steps=tuple(steps),
        ),
        AGENTS,
    )
    assert isinstance(got, Frozen), got
    return got


def _agent(id: str, brief: str, reads=()) -> AgentStep:
    return AgentStep(
        id=id,
        agent="backend.diagnose",
        toolsets=("backend.logs",),
        brief=brief,
        reads=tuple(reads),
    )


class Script:
    """The ports, scripted: `answers` maps a brief to what the agent returns
    (a list is one answer per attempt; an exception raises), `plans` is what
    the Planner returns, in turn."""

    def __init__(self, answers=None, plans=()) -> None:
        self.answers = dict(answers or {})
        self.plans = list(plans)
        self.agent_calls: list[tuple[str, dict]] = []
        self.draft_calls: list[dict] = []
        self.planner_calls: list[tuple[Frozen, dict, Replan]] = []

    async def agent(self, step, reads, resume=None):
        self.agent_calls.append((step.brief, dict(reads)))
        answer = self.answers[step.brief]
        if isinstance(answer, list):
            answer = answer.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def draft(self, step, reads):
        self.draft_calls.append(dict(reads))
        return Reply(f"drafted from {', '.join(sorted(reads))}")

    async def planner(self, frozen, results, replan):
        self.planner_calls.append((frozen, dict(results), replan))
        return self.plans.pop(0)

    def steps(self) -> Steps:
        return Steps(agent=self.agent, draft=self.draft, planner=self.planner)


async def _run(db, frozen, script, **kwargs):
    return await run_plan(
        db,
        frozen,
        agents=AGENTS,
        placement_identity=PLACEMENT,
        steps=script.steps(),
        **kwargs,
    )


# ---- the walk -------------------------------------------------------------


async def test_a_plan_runs_its_steps_in_order_and_hands_results_to_readers(db):
    script = Script({"look": Found("email rule", ["L3"])})
    frozen = _frozen(_agent("p1", "look"), DraftStep(id="p2", reads=("p1",)))

    end = await _run(db, frozen, script)

    assert end.outcome == Reply("drafted from p1")
    assert script.draft_calls == [{"p1": Found("email rule", ["L3"])}]
    assert end.plans == (frozen,) and end.replans_used == 0


async def test_a_stored_result_is_never_run_again_and_comes_back_as_its_type(db):
    script = Script({"look": Found("email rule", ["L3"])})
    frozen = _frozen(_agent("p1", "look"), DraftStep(id="p2", reads=("p1",)))

    await _run(db, frozen, script)
    end = await _run(db, frozen, script)

    assert len(script.agent_calls) == 1
    # The second walk read the stored `Reply` too: the draft was not rewritten.
    assert len(script.draft_calls) == 1
    assert end.outcome == Reply("drafted from p1")


async def test_a_changed_placement_runs_the_step_again(db):
    script = Script({"look": Found("x")})
    frozen = _frozen(_agent("p1", "look"), DraftStep(id="p2", reads=("p1",)))

    await _run(db, frozen, script)
    await run_plan(
        db,
        frozen,
        agents=AGENTS,
        placement_identity=("dev", "other"),
        steps=script.steps(),
    )

    assert len(script.agent_calls) == 2


@pytest.mark.parametrize(
    "outcome",
    [
        Ask("which correlationId?", history=[{"kind": "request"}]),
        HandOver("nothing to read"),
        Retriage(reason="it is a question", found="L2 says so"),
    ],
)
async def test_an_agent_outcome_is_stored_and_stops_the_plan(db, outcome):
    script = Script({"look": outcome})
    frozen = _frozen(_agent("p1", "look"), DraftStep(id="p2", reads=("p1",)))

    end = await _run(db, frozen, script)
    again = await _run(db, frozen, script)

    assert end.outcome == outcome and again.outcome == outcome
    assert script.draft_calls == [] and len(script.agent_calls) == 1


async def test_a_stored_ask_keeps_its_history_and_its_evidence(db):
    """Ticket 14: the `Evidence` is stored with the `Ask`, so a continuation
    numbers on from the same place."""
    evidence = Evidence()
    evidence.show(["ERROR email invalid"])
    script = Script({"look": Ask("which id?", history=[{"a": 1}], evidence=evidence)})
    frozen = _frozen(_agent("p1", "look"), DraftStep(id="p2", reads=("p1",)))

    await _run(db, frozen, script)
    end = await _run(db, frozen, script)

    assert end.outcome.history == [{"a": 1}]
    assert end.outcome.evidence.index == {"L1": "ERROR email invalid"}
    assert end.outcome.evidence.show(["next"]) == "L2 | next"


# ---- passes (ticket 14) -----------------------------------------------------


class Replying(Script):
    """A script whose reporter said `reply` since the question."""

    def __init__(self, answers=None, plans=(), reply="the id is 42") -> None:
        super().__init__(answers, plans)
        self.reply = reply
        self.resumes: list = []

    async def agent(self, step, reads, resume=None):
        self.resumes.append(resume)
        return await super().agent(step, reads, resume)

    async def replied(self, since):
        return self.reply

    def steps(self) -> Steps:
        return Steps(
            agent=self.agent,
            draft=self.draft,
            planner=self.planner,
            replied=self.replied,
        )


async def test_a_later_pass_continues_the_agents_ask_with_the_reply(db):
    asked = Ask("which id?", history=[{"a": 1}])
    script = Replying({"look": [asked, Found("email rule", ["L3"])]})
    frozen = _frozen(_agent("p1", "look"), DraftStep(id="p2", reads=("p1",)))

    await _run(db, frozen, script, pass_no=1)
    end = await _run(db, frozen, script, pass_no=2)

    assert script.resumes == [None, Resume(asked, "the id is 42")]
    assert end.outcome == Reply("drafted from p1")


async def test_the_same_pass_reuses_its_own_ask(db):
    """A crash re-run of the pass that asked must not answer its own question."""
    script = Replying({"look": Ask("which id?", history=[{"a": 1}])})
    frozen = _frozen(_agent("p1", "look"), DraftStep(id="p2", reads=("p1",)))

    await _run(db, frozen, script, pass_no=1)
    await _run(db, frozen, script, pass_no=1)

    assert len(script.agent_calls) == 1


async def test_the_planners_answered_question_is_a_replan(db):
    nxt = _frozen(_agent("p1", "look"), DraftStep(id="p2", reads=("p1",)), version=2)
    script = Replying({"look": Found("x")}, plans=[nxt])
    frozen = _frozen(AskStep(id="p1", question="which env?"))

    await _run(db, frozen, script, pass_no=1)
    end = await _run(db, frozen, script, pass_no=2)

    ((_, _, signal),) = script.planner_calls
    assert signal.found == "the id is 42"
    assert end.replans_used == 1 and end.outcome == Reply("drafted from p1")


async def test_a_hand_over_from_an_earlier_pass_is_run_again(db):
    script = Replying({"look": [HandOver("stuck"), Found("x")]})
    frozen = _frozen(_agent("p1", "look"), DraftStep(id="p2", reads=("p1",)))

    await _run(db, frozen, script, pass_no=1)
    end = await _run(db, frozen, script, pass_no=2)

    assert script.resumes == [None, None]
    assert end.outcome == Reply("drafted from p1")


async def test_a_result_from_an_earlier_pass_is_reused(db):
    script = Replying({"look": Found("x")})
    frozen = _frozen(_agent("p1", "look"), DraftStep(id="p2", reads=("p1",)))

    await _run(db, frozen, script, pass_no=1)
    await _run(db, frozen, script, pass_no=2)

    assert len(script.agent_calls) == 1


async def test_the_planners_ask_and_hand_over_steps_stop_the_plan(db):
    asked = await _run(db, _frozen(AskStep(id="p1", question="which env?")), Script())
    handed = await _run(
        db, _frozen(HandOverStep(id="p1", reason="prod write")), Script()
    )

    assert asked.outcome == Ask("which env?")
    assert handed.outcome == HandOver("prod write")


# ---- step_failed ------------------------------------------------------------


async def test_a_step_failing_every_attempt_hands_over_step_failed(db):
    script = Script({"look": [RuntimeError("boom sk-secret")] * STEP_ATTEMPTS})
    frozen = _frozen(_agent("p1", "look"), DraftStep(id="p2", reads=("p1",)))

    end = await _run(db, frozen, script)

    assert STEP_ATTEMPTS == 2
    assert len(script.agent_calls) == STEP_ATTEMPTS
    assert end.outcome.reason.startswith("step_failed: p1")
    assert "sk-secret" not in end.outcome.reason
    # Not stored: the next pass tries the step again.
    assert await db.step_results(7) == {}


async def test_a_step_that_fails_once_is_tried_again(db):
    script = Script({"look": [RuntimeError("blip"), Found("ok")]})
    frozen = _frozen(_agent("p1", "look"), DraftStep(id="p2", reads=("p1",)))

    end = await _run(db, frozen, script)

    assert end.outcome == Reply("drafted from p1")
    assert len(script.agent_calls) == 2


# ---- Replan and its reuse (ticket 13 §6) ------------------------------------

WRONG = Replan(reason="the 400 is upstream", found="L4: gateway rejects it")


async def test_change_direction_runs_the_new_step_and_not_the_old_one(db):
    v2 = _frozen(
        _agent("p1b", "look upstream"), DraftStep(id="p2", reads=("p1b",)), version=2
    )
    script = Script({"look": WRONG, "look upstream": Found("gateway")}, plans=[v2])
    v1 = _frozen(_agent("p1", "look"), DraftStep(id="p2", reads=("p1",)))

    end = await _run(db, v1, script)

    assert [b for b, _ in script.agent_calls] == ["look", "look upstream"]
    assert script.draft_calls == [{"p1b": Found("gateway")}]
    assert end.plans == (v1, v2) and end.replans_used == 1
    frozen, results, replan = script.planner_calls[0]
    assert (frozen, results, replan) == (v1, {"p1": WRONG}, WRONG)


async def test_build_on_reuses_the_replan_as_data_for_the_next_step(db):
    v2 = _frozen(
        _agent("p1", "look"),
        _agent("p1b", "look upstream", reads=["p1"]),
        DraftStep(id="p2", reads=("p1b",)),
        version=2,
    )
    script = Script({"look": WRONG, "look upstream": Found("gateway")}, plans=[v2])
    v1 = _frozen(_agent("p1", "look"), DraftStep(id="p2", reads=("p1",)))

    end = await _run(db, v1, script)

    assert [b for b, _ in script.agent_calls] == ["look", "look upstream"]
    assert script.agent_calls[1][1] == {"p1": WRONG}
    assert end.outcome == Reply("drafted from p1b") and end.replans_used == 1


async def test_stop_drafts_from_the_reused_replan(db):
    v2 = _frozen(_agent("p1", "look"), DraftStep(id="p2", reads=("p1",)), version=2)
    script = Script({"look": WRONG}, plans=[v2])
    v1 = _frozen(_agent("p1", "look"), DraftStep(id="p2", reads=("p1",)))

    end = await _run(db, v1, script)

    assert len(script.agent_calls) == 1
    assert script.draft_calls == [{"p1": WRONG}]
    assert end.replans_used == 1 and len(script.planner_calls) == 1


async def test_a_planner_that_returns_the_same_plan_cannot_loop(db):
    v1 = _frozen(_agent("p1", "look"), DraftStep(id="p2", reads=("p1",)))
    same = _frozen(_agent("p1", "look"), DraftStep(id="p2", reads=("p1",)), version=2)
    script = Script({"look": WRONG}, plans=[same])

    end = await _run(db, v1, script)

    assert end.outcome == Reply("drafted from p1")
    assert len(script.agent_calls) == 1 and len(script.planner_calls) == 1


async def test_a_replan_stored_before_the_planner_answered_is_still_the_signal(db):
    """A walk cut between storing the `Replan` and the Planner's answer (a
    crash, ticket 14) must not run on through the plan the agent rejected."""
    v1 = _frozen(_agent("p1", "look"), DraftStep(id="p2", reads=("p1",)))
    crashed = Script({"look": WRONG}, plans=[RuntimeError("process died")])
    crashed.planner = _raising(crashed.planner)
    with pytest.raises(RuntimeError):
        await _run(db, v1, crashed)

    v2 = _frozen(
        _agent("p1b", "look upstream"), DraftStep(id="p2", reads=("p1b",)), version=2
    )
    resumed = Script({"look upstream": Found("gateway")}, plans=[v2])
    end = await _run(db, v1, resumed)

    assert resumed.draft_calls == [{"p1b": Found("gateway")}]
    assert [b for b, _ in resumed.agent_calls] == ["look upstream"]
    assert end.replans_used == 1


def _raising(planner):
    async def wrapped(frozen, results, replan):
        got = await planner(frozen, results, replan)
        if isinstance(got, Exception):
            raise got
        return got

    return wrapped


async def test_a_planner_hand_over_ends_the_run(db):
    script = Script({"look": WRONG}, plans=[HandOver("planner_failed: refused twice")])
    v1 = _frozen(_agent("p1", "look"), DraftStep(id="p2", reads=("p1",)))

    end = await _run(db, v1, script)

    assert end.outcome == HandOver("planner_failed: refused twice")


# ---- replans_exhausted --------------------------------------------------------


async def test_replans_past_max_replans_hand_over_replans_exhausted(db):
    last = Replan(reason="still wrong", found="L9: the proxy")
    v2 = _frozen(
        _agent("p1b", "look upstream"),
        DraftStep(id="p2", reads=("p1b",)),
        version=2,
        max_replans=1,
    )
    script = Script({"look": WRONG, "look upstream": last}, plans=[v2])
    v1 = _frozen(_agent("p1", "look"), DraftStep(id="p2", reads=("p1",)), max_replans=1)

    end = await _run(db, v1, script)

    assert end.outcome == HandOver("replans_exhausted: L9: the proxy")
    assert end.plans == (v1, v2) and end.replans_used == 1
    assert len(script.planner_calls) == 1
    kinds = sorted(row.kind for row in (await db.step_results(7)).values())
    assert kinds == ["replan", "replan"]


async def test_replans_already_used_count_toward_max_replans(db):
    script = Script({"look": WRONG})
    v1 = _frozen(_agent("p1", "look"), DraftStep(id="p2", reads=("p1",)), max_replans=2)

    end = await _run(db, v1, script, replans_used=2)

    assert end.outcome.reason.startswith("replans_exhausted:")
    assert script.planner_calls == []


# ---- the guard ------------------------------------------------------------------


def test_only_the_runner_writes_step_results():
    """`put_step_result` is called from `runner.py` alone, and only the plans
    repository names the mapped class — a second writer could store a result
    the runner's reuse rule never agreed to."""
    writers, mappers = [], []
    for path in [*ROOT.glob("friday/**/*.py"), *ROOT.glob("plugins/**/*.py")]:
        text = path.read_text()
        rel = path.relative_to(ROOT).as_posix()
        if re.search(r"\bput_step_result\(", text):
            writers.append(rel)
        if re.search(r"\bschema\.StepResult\b", text):
            mappers.append(rel)
    assert sorted(writers) == [
        "friday/kernel/spine/runner.py",
        "friday/store/repositories/plans.py",
    ]
    assert mappers == ["friday/store/repositories/plans.py"]
