"""build-the-spine ticket 11 — the Planner.

One core agent writes every plan; GatePlan refuses what breaks the rules. A
refusal goes back into the same conversation for `PLAN_REWRITES` more tries,
then `PlannerFailed` (`planner_failed`). A replan is a fresh conversation
with its own rewrites (board `domains-plug-in`, tickets 11, 12, 17).
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from friday.kernel.config import AgentConfig
from friday.kernel.spine.plan import AgentStep, DraftStep, step_keys
from friday.kernel.spine.plan_gate import Frozen
from friday.kernel.spine.planner import (
    PLAN_REWRITES,
    PlannerFailed,
    Planning,
    plan,
    replan,
)
from friday.sdk.action import Action, ActionContract, Limits, Recognition
from friday.sdk.actions import HandOver, Replan
from friday.sdk.agent import AgentSpec, Budget
from friday.sdk.intake import Hints, IntakeContext
from friday.sdk.testing import (
    FunctionModel,
    ModelResponse,
    ScriptedModel,
    TextPart,
    ToolCallPart,
    function_call,
)
from friday.sdk.toolset import RunContext, ToolsetSpec, tool


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    from friday.kernel.harness import harness as harness_module

    monkeypatch.setattr(harness_module, "PROVIDER_BACKOFF_SECONDS", 0.0)


@dataclass
class Diagnosis:
    cause: str = ""


CONFIG = AgentConfig(
    name="planner",
    api_key="sk-secret",
    base_url="https://example.invalid/v1",
    model="test-model",
    max_turns=4,
    tokens=100_000,
)
ACTION = Action(
    name="backend.trace_problem",
    recognition=Recognition(means="m", pick_when=("p",)),
    contract=ActionContract(
        allowed_step_types=frozenset({"agent", "ask", "hand_over", "draft"}),
        allowed_agents=frozenset({"backend.diagnose"}),
        allowed_toolsets=frozenset({"backend.logs", "backend.code"}),
        constraints=("every ref points at a line that was read",),
        approval_policy="a",
        acceptance_template="a cause",
        limits=Limits(max_replans=2, max_steps=3),
    ),
    planning="correlationId given → have diagnose find it first",
)
DIAGNOSE = AgentSpec(
    name="backend.diagnose",
    description="Finds what caused a failing request.",
    instructions="i",
    result=Diagnosis,
    tier="flash",
    toolsets=("backend.logs", "backend.code", "backend.db"),
    budget=Budget(max_turns=20, tokens=500_000),
    temperature=0.0,
)
EXPLAIN = AgentSpec(
    name="backend.explain",
    description="Explains code; not allowed by this action.",
    instructions="i",
    result=Diagnosis,
    tier="flash",
    toolsets=("backend.code",),
    budget=Budget(max_turns=5, tokens=1000),
    temperature=0.0,
)
INTAKE = IntakeContext(
    request_text="POST /v1/orders returns 500, correlationId 1234",
    reported_at="2026-09-29T10:00:00+07:00",
    hints=Hints(),
    memory=("orders-api logs live in Loki",),
    skills=("trace-a-request: follow one request through the logs",),
)


#: What each fake toolset's factory builds: a read the Planner keeps and,
#: for memory, a write it must never be handed.
_TOOLS = {
    "core.memory": ("memory_search", "memory_delete"),
    "core.skills": ("search_skills",),
    "backend.logs": ("read_log",),
    "backend.code": ("read_code",),
}


def _toolset(name: str, description: str, built: list[str]) -> ToolsetSpec:
    def factory(run: RunContext) -> list:
        built.append(name)

        def probe(query: str) -> str:
            """Look something up.

            Args:
                query: what to look up.
            """
            return f"{name}: nothing about {query}"

        return [tool(probe, name=n) for n in _TOOLS[name]]

    return ToolsetSpec(name=name, description=description, factory=factory)


def _planning(model, built: list[str] | None = None) -> Planning:
    built = [] if built is None else built
    toolsets = {
        name: _toolset(name, f"{name} description", built)
        for name in ("core.memory", "core.skills", "backend.logs", "backend.code")
    }
    return Planning(
        config=CONFIG,
        task_id=7,
        action=ACTION,
        intake=INTAKE,
        agents={"backend.diagnose": DIAGNOSE, "backend.explain": EXPLAIN},
        toolsets=toolsets,
        model=model,
    )


GOOD = {
    "goal": "find why orders 500",
    "steps": [
        {
            "id": "s1",
            "type": "agent",
            "agent": "backend.diagnose",
            "toolsets": ["backend.logs", "backend.code"],
            "brief": "find correlationId 1234 in the logs first",
        },
        {"id": "s2", "type": "draft", "reads": ["s1"]},
    ],
}
#: An agent the contract does not allow: a contract error, not a shape one.
BAD = {
    "goal": "explain",
    "steps": [
        {"id": "s1", "type": "agent", "agent": "backend.explain", "brief": "b"},
        {"id": "s2", "type": "draft", "reads": ["s1"]},
    ],
}


def _answer(data: dict) -> list:
    return [function_call("answer", data, call_id=f"answer-{id(data)}")]


def _texts(request) -> str:
    """Every text the model was handed in one request, joined."""
    out = []
    for message in request.input:
        for part in message.parts:
            content = getattr(part, "content", None)
            if isinstance(content, str):
                out.append(content)
    return "\n".join(out)


async def test_a_plan_that_passes_is_frozen_as_version_one():
    model = ScriptedModel([_answer(GOOD)])

    got = await plan(_planning(model))

    assert isinstance(got, Frozen)
    assert got.plan.plan_version == 1 and got.plan.replaces is None
    assert got.plan.task_id == 7 and got.plan.action == "backend.trace_problem"
    assert got.plan.contract == ACTION.contract
    assert got.plan.steps == (
        AgentStep(
            "s1",
            "backend.diagnose",
            ("backend.code", "backend.logs"),
            "find correlationId 1234 in the logs first",
        ),
        DraftStep("s2", ("s1",)),
    )
    assert len(model.calls) == 1


async def test_an_agent_step_with_no_toolsets_freezes_with_the_full_grant():
    """`contract ∩ ceiling`, filled before the plan is hashed — so the key and
    the stored plan carry the real grant, and equal the spelled-out plan's."""
    bare = {
        **GOOD,
        "steps": [{**GOOD["steps"][0], "toolsets": []}, GOOD["steps"][1]],
    }

    got = await plan(_planning(ScriptedModel([_answer(bare)])))
    spelled = await plan(_planning(ScriptedModel([_answer(GOOD)])))

    assert isinstance(got, Frozen) and isinstance(spelled, Frozen)
    assert got.plan.steps[0].toolsets == ("backend.code", "backend.logs")
    assert step_keys(got.plan, ("x",)) == step_keys(spelled.plan, ("x",))


async def test_a_toolset_outside_the_ceiling_is_still_refused_never_clipped():
    over = {
        **GOOD,
        "steps": [
            {**GOOD["steps"][0], "toolsets": ["backend.logs", "backend.db"]},
            GOOD["steps"][1],
        ],
    }

    got = await plan(_planning(ScriptedModel([_answer(over)] * 3)))

    assert isinstance(got, PlannerFailed)
    assert any("backend.db" in e for e in got.errors[0])


async def test_a_refusal_goes_back_in_the_same_conversation_and_the_rewrite_passes():
    model = ScriptedModel([_answer(BAD), _answer(GOOD)])

    got = await plan(_planning(model))

    assert isinstance(got, Frozen)
    assert got.plan.plan_version == 1
    rewrite = model.calls[1]
    said = _texts(rewrite)
    # The same conversation: the opening message and the refused answer are
    # still there, and the errors arrive after them.
    assert "POST /v1/orders returns 500" in said
    assert any(
        isinstance(part, ToolCallPart) and part.args == BAD
        for message in rewrite.input
        for part in message.parts
    )
    assert "GatePlan refused your plan" in said
    assert "agent backend.explain is not allowed by the contract" in said


async def test_rewrites_spent_is_planner_failed_with_every_version_and_error():
    model = ScriptedModel([_answer(BAD)] * (1 + PLAN_REWRITES))

    got = await plan(_planning(model))

    assert isinstance(got, PlannerFailed)
    assert isinstance(got, HandOver)
    assert got.reason.startswith("planner_failed")
    assert len(got.plans) == len(got.errors) == 1 + PLAN_REWRITES
    assert all(p is not None and p.goal == "explain" for p in got.plans)
    assert all(
        any("backend.explain is not allowed" in e for e in errors)
        for errors in got.errors
    )
    assert len(model.calls) == 1 + PLAN_REWRITES


async def test_no_answer_at_all_spends_a_rewrite_like_a_refusal():
    model = ScriptedModel([[TextPart(content="I cannot plan this.")]])

    got = await plan(_planning(model))

    assert isinstance(got, PlannerFailed)
    assert got.plans == (None,) * (1 + PLAN_REWRITES)
    assert all(e[0].startswith("no plan came back") for e in got.errors)


async def test_planner_failed_says_what_the_planner_read():
    model = ScriptedModel(
        [
            [function_call("memory_search", {"query": "orders"}, call_id="m1")],
            *[_answer(BAD)] * (1 + PLAN_REWRITES),
        ]
    )

    got = await plan(_planning(model))

    assert isinstance(got, PlannerFailed)
    assert got.read == ('memory_search({"query": "orders"})',)


async def test_it_gets_only_the_reads_of_core_memory_and_core_skills():
    built: list[str] = []
    seen: list = []

    def reply(messages, info):
        seen.append(info)
        return ModelResponse(parts=_answer(GOOD))

    await plan(_planning(FunctionModel(reply), built))

    assert sorted(built) == ["core.memory", "core.skills"]
    offered = {t.name for t in seen[0].function_tools}
    assert offered == {"memory_search", "search_skills"}  # no memory_delete


async def test_without_its_toolsets_the_planner_refuses_to_start():
    p = _planning(ScriptedModel([_answer(GOOD)]))
    toolsets = {k: v for k, v in p.toolsets.items() if k != "core.skills"}
    with pytest.raises(ValueError, match="core.skills"):
        await plan(Planning(**{**_fields(p), "toolsets": toolsets}))


def _fields(p: Planning) -> dict:
    return {f: getattr(p, f) for f in Planning.__dataclass_fields__}


async def test_after_a_run_with_no_answer_the_refusal_still_names_the_plan_and_errors():
    """Refused → nothing came back (its messages lost) → the next message
    still shows the refused plan and its errors."""
    model = ScriptedModel(
        [
            _answer(BAD),
            [TextPart(content="thinking")],
            [TextPart(content="x")],
            _answer(GOOD),
        ]
    )

    got = await plan(_planning(model))

    assert isinstance(got, Frozen)
    said = _texts(model.calls[-1])
    assert "no plan came back" in said
    assert "backend.explain is not allowed by the contract" in said
    assert '"goal": "explain"' in said


async def test_the_opening_message_names_what_the_planner_may_use():
    model = ScriptedModel([_answer(GOOD)])

    await plan(_planning(model))

    said = _texts(model.calls[0])
    assert "correlationId given → have diagnose find it first" in said  # planning
    assert "orders-api logs live in Loki" in said  # retrieved memory
    assert "trace-a-request" in said  # retrieved skills
    assert "Finds what caused a failing request." in said  # agent description
    assert "returns: Diagnosis" in said
    assert "ask_reporter, hand_over, replan, retriage" in said
    assert "20 turns, 500000 tokens" in said
    assert "backend.logs: backend.logs description" in said
    # Only the contract ∩ the agent's ceiling, and only allowed agents.
    assert "backend.db" not in said
    assert "backend.explain" not in said


async def test_a_replan_is_a_fresh_conversation_with_its_own_rewrites():
    first = await plan(_planning(ScriptedModel([_answer(GOOD)])))
    assert isinstance(first, Frozen)
    model = ScriptedModel([_answer(BAD), _answer(BAD), _answer(GOOD)])
    signal = Replan(reason="the logs show a timeout upstream", found="L3: timeout")

    got = await replan(
        _planning(model), first, {"s1": Diagnosis(cause="timeout")}, signal
    )

    assert isinstance(got, Frozen)
    assert got.plan.plan_version == 2
    assert got.plan.replaces == first.plan_hash
    opening = model.calls[0]
    assert len(opening.input) == 1  # nothing carried from the first plan's run
    said = _texts(opening)
    assert "<current_plan>" in said and '"version": 1' in said
    assert "find correlationId 1234 in the logs first" in said
    assert '- s1: {"cause": "timeout"}' in said
    assert "the logs show a timeout upstream" in said and "L3: timeout" in said


def test_the_instructions_ask_for_a_goal_and_not_a_method():
    """Ticket 20: `brief` is what to establish and what the reporter gave; the
    agent's own instructions say how. Empty `toolsets` is the full grant."""
    from friday.kernel.spine.planner_prompt import INSTRUCTIONS

    said = " ".join(INSTRUCTIONS.split())

    assert "never a method" in said
    assert "where to look first" not in said
    assert "Leave `toolsets` empty for the full grant" in said
