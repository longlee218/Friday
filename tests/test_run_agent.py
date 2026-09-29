"""build-the-spine ticket 10 — the Harness runs an `AgentSpec`.

`run_agent` is the one core entry that runs any declared agent: toolsets are
contract ∩ `spec.toolsets`, built per run; the core's terminal tools end the
run with an outcome; the budget stops it; a stored `Ask` is a continuation
point (board `domains-plug-in`, tickets 03, 13, 14, 16, 17).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from dataclasses import dataclass, field

import pytest

from friday.kernel.config import TierConfig
from friday.kernel.harness.harness import OUTPUT_CORRECTIONS
from friday.kernel.harness.run_agent import AgentRunFailed, run_agent
from friday.sdk.action import ActionContract, Limits
from friday.sdk.actions import Ask, HandOver, Replan, Retriage
from friday.sdk.agent import AgentSpec, Budget
from friday.sdk.testing import (
    FunctionModel,
    ModelResponse,
    ScriptedModel,
    Usage,
    function_call,
)
from friday.sdk.toolset import RunContext, ToolsetSpec, tool
from plugins.backend.toolsets.evidence import Evidence


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    from friday.kernel.harness import harness as harness_module

    monkeypatch.setattr(harness_module, "PROVIDER_BACKOFF_SECONDS", 0.0)


@dataclass
class Found:
    summary: str = field(default="", metadata={"doc": "what was found"})
    refs: list[str] = field(default_factory=list, metadata={"doc": "line ids"})


TIER = TierConfig(
    name="flash", api_key="sk-secret", base_url="https://example.invalid/v1",
    model="test-model",
)
LOG = ["GET /users 400 email invalid", "GET /users 200 ok"]


class Reads:
    """A toolset whose one tool shows a log line through the run's Evidence,
    counting how often it was called."""

    def __init__(self) -> None:
        self.calls = 0
        self.run: RunContext | None = None

    def factory(self, run: RunContext) -> list:
        self.run = run
        def read_line(n: int) -> str:
            """Read one log line.

            Args:
                n: which line, from 0.
            """
            self.calls += 1
            return run.evidence.show([LOG[n]])

        return [tool(read_line)]


def _named(name: str, calls: list[str]):
    def factory(run: RunContext) -> list:
        calls.append(name)

        def probe() -> str:
            """Probe."""
            return name

        return [tool(probe, name=name.replace(".", "_"))]

    return ToolsetSpec(name=name, description="d", factory=factory)


def _spec(toolsets=("demo.reads",), max_turns=4, tokens=100_000) -> AgentSpec:
    return AgentSpec(
        name="demo.probe", description="d", instructions="find it",
        result=Found, tier="flash", toolsets=tuple(toolsets),
        budget=Budget(max_turns=max_turns, tokens=tokens), temperature=0,
    )


def _contract(toolsets=("demo.reads",), steps=("agent", "ask", "draft")):
    return ActionContract(
        allowed_step_types=frozenset(steps),
        allowed_agents=frozenset({"demo.probe"}),
        allowed_toolsets=frozenset(toolsets),
        constraints=(), approval_policy="always", acceptance_template="",
        limits=Limits(max_replans=2, max_steps=5),
    )


def _context() -> RunContext:
    return RunContext(task_id=7, domain=None, evidence=Evidence(), mcp={},
                      reported_at=datetime(2026, 9, 29, tzinfo=timezone.utc))


class Offered(FunctionModel):
    """Records the tool and output-tool names each request offered, then
    answers with `parts`."""

    def __init__(self, *parts) -> None:
        self.tools: list[str] = []
        self.outputs: list[str] = []

        def reply(messages, info):
            self.tools = [t.name for t in info.function_tools]
            self.outputs = [t.name for t in info.output_tools]
            return ModelResponse(parts=list(parts))

        super().__init__(reply, model_name="test-model")


async def _run(model, *, spec=None, contract=None, toolsets=None, context=None,
               brief="look", history=None):
    reads = Reads()
    got = await run_agent(
        spec or _spec(), TIER, contract or _contract(),
        toolsets if toolsets is not None else
        [ToolsetSpec(name="demo.reads", description="d", factory=reads.factory)],
        context or _context(), brief, history, model=model,
    )
    return got, reads


# --- each outcome is reachable -------------------------------------------


async def test_the_declared_result_ends_the_run():
    got, _ = await _run(ScriptedModel([[function_call(
        "answer", {"summary": "email check", "refs": []})]]))

    assert got == Found(summary="email check", refs=[])


async def test_ask_reporter_ends_the_run_with_an_ask():
    context = _context()
    got, _ = await _run(
        ScriptedModel([[function_call("ask_reporter", {"question": "which env?"})]]),
        context=context,
    )

    assert isinstance(got, Ask) and got.text == "which env?"
    assert got.evidence is context.evidence
    # Stored at `(task_id, step_key)` by the runner, so it must be plain data.
    assert json.loads(json.dumps(got.history))


async def test_hand_over_ends_the_run_with_a_hand_over():
    got, _ = await _run(ScriptedModel([[function_call(
        "hand_over", {"reason": "needs prod access"})]]))

    assert got == HandOver("needs prod access")


async def test_replan_ends_the_run_with_a_replan():
    got, _ = await _run(ScriptedModel([[function_call(
        "replan", {"reason": "not the gateway", "found": "L1 is a 400"})]]))

    assert got == Replan(reason="not the gateway", found="L1 is a 400")


async def test_retriage_ends_the_run_with_a_retriage():
    got, _ = await _run(ScriptedModel([[function_call(
        "retriage", {"reason": "it is failing", "found": "L1 is a 400"})]]))

    assert got == Retriage(reason="it is failing", found="L1 is a 400")


async def test_every_agent_gets_the_four_terminal_tools():
    model = Offered(function_call("hand_over", {"reason": "x"}))
    await _run(model)

    assert {"ask_reporter", "hand_over", "replan", "retriage"} <= set(model.outputs)


async def test_ask_reporter_is_dropped_when_the_contract_has_no_ask():
    model = Offered(function_call("hand_over", {"reason": "x"}))
    await _run(model, contract=_contract(steps=("agent", "draft")))

    assert "ask_reporter" not in model.outputs
    assert {"hand_over", "replan", "retriage"} <= set(model.outputs)


# --- toolsets --------------------------------------------------------------


async def test_toolsets_are_the_contract_s_intersected_with_the_agent_s():
    built: list[str] = []
    toolsets = [_named(n, built) for n in ("demo.both", "demo.agent_only", "demo.contract_only")]
    model = Offered(function_call("hand_over", {"reason": "x"}))

    await _run(
        model,
        spec=_spec(toolsets=("demo.both", "demo.agent_only")),
        contract=_contract(toolsets=("demo.both", "demo.contract_only")),
        toolsets=toolsets,
    )

    assert built == ["demo.both"]
    assert model.tools == ["demo_both"]


async def test_a_factory_is_handed_the_run_s_context():
    seen = []

    def factory(run):
        seen.append(run)
        return []

    context = _context()
    await _run(
        Offered(function_call("hand_over", {"reason": "x"})), context=context,
        toolsets=[ToolsetSpec(name="demo.reads", description="d", factory=factory)],
    )

    assert seen == [context]


# --- continuing from a stored Ask -------------------------------------------


async def test_continuing_from_an_ask_keeps_line_ids_and_repeats_no_read():
    """Carried from build-the-loop ticket 04: the reply continues the stored
    run — the first read is in the history, not read again, and the next
    read's id carries on from it."""
    first = ScriptedModel([
        [function_call("read_line", {"n": 0}, call_id="r0")],
        [function_call("ask_reporter", {"question": "which env?"})],
    ])
    ask, reads = await _run(first)
    assert isinstance(ask, Ask)
    assert ask.evidence.index == {"L1": LOG[0]}

    second = ScriptedModel([
        [function_call("read_line", {"n": 1}, call_id="r1")],
        [function_call("answer", {"summary": "400 on email", "refs": ["L1", "L2"]})],
    ])
    reads_again = Reads()
    got = await run_agent(
        _spec(), TIER, _contract(),
        [ToolsetSpec(name="demo.reads", description="d", factory=reads_again.factory)],
        _context(), "prod", ask, model=second,
    )

    assert got == Found(summary="400 on email", refs=["L1", "L2"])
    # The first request of the continued run already holds the first read and
    # ends with the reply.
    sent = "\n".join(
        str(getattr(part, "content", ""))
        for message in second.calls[0].input for part in message.parts
    )
    assert f"L1 | {LOG[0]}" in sent
    assert sent.rstrip().endswith("prod")
    # One new read, numbered after the stored one; L1 unchanged.
    assert reads_again.calls == 1
    assert reads_again.run.evidence.index == {"L1": LOG[0], "L2": LOG[1]}
    # The stored Ask is untouched, so a re-run from it numbers the same way.
    assert ask.evidence.index == {"L1": LOG[0]}


# --- budget -----------------------------------------------------------------


def _forever(usage: Usage | None = None) -> FunctionModel:
    """Calls a tool every turn and never finishes."""
    state = {"n": 0}

    def reply(messages, info):
        state["n"] += 1
        return ModelResponse(
            parts=[function_call("read_line", {"n": 0}, call_id=f"c{state['n']}")],
            usage=usage or Usage(),
        )

    model = FunctionModel(reply, model_name="test-model")
    model.requests = state  # type: ignore[attr-defined]
    return model


async def test_over_max_turns_stops_the_run_with_a_hand_over():
    model = _forever()
    got, _ = await _run(model, spec=_spec(max_turns=3))

    assert isinstance(got, HandOver) and got.reason.startswith("budget_spent")
    # The output correction rides on top of max_turns: an attempt, not a
    # turn (docs/DESIGN.md, the harness's budget rule).
    assert model.requests["n"] == 3 + OUTPUT_CORRECTIONS


async def test_over_tokens_stops_the_run_with_a_hand_over():
    model = _forever(Usage(input_tokens=600, output_tokens=0))
    got, _ = await _run(model, spec=_spec(max_turns=50, tokens=1000))

    assert isinstance(got, HandOver) and got.reason.startswith("budget_spent")
    assert model.requests["n"] == 2


async def test_a_failure_that_is_not_the_budget_raises():
    def reply(messages, info):
        raise RuntimeError("provider exploded")

    with pytest.raises(AgentRunFailed):
        await _run(FunctionModel(reply, model_name="test-model"))
