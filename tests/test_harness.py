"""Ticket 14 — one place an agent is run (on Pydantic AI since ticket 05).

Everything every agent needs and none of them should restate: the client, the
settings, the logging hooks, the caps, and the rule that a failure becomes work
for a person rather than an exception nobody catches.

Written after the second agent existed, not before. One agent is a hypothetical
seam; building this against triage alone would have been guessing at what
varies.
"""

from __future__ import annotations

import asyncio

import pytest
from friday.sdk.testing import (
    Agent,
    FunctionModel,
    ModelResponse,
    ScriptedModel,
    TextPart,
    assistant_message,
    function_call,
)

from friday.kernel.harness.harness import Harness, ToolContext, tool
from friday.kernel.harness.mcp import name_of
from friday.kernel.config import AgentConfig

CONFIG = AgentConfig(
    name="an-agent", api_key="sk-secret", base_url="https://example.invalid/v1",
    model="test-model", max_turns=1, settings={"temperature": 0}, options={},
)


def harness(*steps, config=None, **kw) -> Harness:
    return Harness(
        config=config or CONFIG,
        instructions="do the thing",
        model=ScriptedModel(list(steps)),
        **kw,
    )


# Module-level so `get_type_hints` resolves the terminal tool's return type, the
# way the real `hand_over(reason) -> HandOver` is module-level. A nested class
# is invisible to `get_type_hints`.
from dataclasses import dataclass as _dataclass


@_dataclass
class _ReplyShape:
    value: str = ""


@_dataclass
class _Escalated:
    reason: str


def _escalate(reason: str) -> _Escalated:
    """Hand this off to a person who can decide (a terminal output tool)."""
    return _Escalated(reason)


class _Counting(FunctionModel):
    """A scripted model that counts the requests it was handed, so a test can
    assert a hiccup was tried again and a rejection was not."""

    def __init__(self, fn) -> None:
        self._n = [0]

        def counted(messages, info):
            self._n[0] += 1
            return fn(messages, info)

        super().__init__(counted, model_name="test-model")

    @property
    def calls(self) -> int:
        return self._n[0]


def _raising(exc) -> _Counting:
    """A model that raises the same thing every request."""

    def fn(messages, info):
        raise exc

    return _Counting(fn)


def _user_text(messages) -> str:
    for message in messages:
        for part in getattr(message, "parts", []):
            if getattr(part, "part_kind", "") == "user-prompt":
                content = part.content
                return content if isinstance(content, str) else str(content)
    return ""


async def test_it_runs_an_agent_and_hands_back_the_result():
    result = await harness([assistant_message("done")]).run("go")

    assert result.output == "done"


async def test_a_failure_is_no_result_rather_than_an_exception():
    """Every agent turns this into its own kind of work — a task for a human,
    or a fall back to a template. None of them should have to catch it."""
    result = await Harness(
        config=CONFIG, instructions="do the thing",
        model=_raising(RuntimeError("provider down")),
    ).run("go")

    assert result is None


async def test_the_reason_it_failed_is_kept():
    run = Harness(
        config=CONFIG, instructions="do the thing",
        model=_raising(RuntimeError("provider down")),
    )
    await run.run("go")

    assert "provider down" in run.last_error


async def test_a_credential_never_appears_in_the_reason():
    """The reason is stored against a task, and a provider exception can quote
    an Authorization header."""
    run = Harness(
        config=CONFIG, instructions="i",
        model=_raising(RuntimeError("401 for Bearer sk-abcdefghijklmnopqrstuvwx")),
    )
    await run.run("go")

    assert "sk-abcdefghijklmnopqrstuvwx" not in run.last_error
    assert "REDACTED" in run.last_error


async def test_both_sides_of_the_call_are_collected():
    calls: list = []

    async def sink(call) -> None:
        calls.append(call)

    await harness([assistant_message("done")], record=sink).run("classify this")

    (call,) = calls
    assert call.agent == "an-agent"
    assert call.model == "test-model"
    assert "classify this" in call.prompt


async def test_configuration_reaches_the_agent():
    """`base_url`, `api_key` and `model` are the whole reason for this shape —
    a different OpenAI-compatible provider must need no code change."""
    built = Harness(config=CONFIG, instructions="do the thing")

    assert isinstance(built.agent, Agent)
    assert built.agent.model.model_name == "test-model"
    assert built.agent.model_settings["temperature"] == 0


async def test_tracing_is_off():
    """Pydantic AI emits no telemetry unless an agent is instrumented, and
    Friday never turns it on. The openai-agents predecessor exported to OpenAI
    using the same key as model requests, which with a third-party provider
    leaked both the traffic and the credential; the equivalent guard now is
    simply that instrumentation stays unset."""
    built = Harness(config=CONFIG, instructions="i")

    assert built.agent.instrument is None


async def test_an_agent_can_be_given_servers_it_did_not_have_to_know_about():
    """Which servers an agent gets is composition, not something it declares —
    the same reason its model and base_url are configuration."""
    from friday.kernel.config import MCPServerConfig
    from friday.kernel.harness.mcp import build

    servers = build(
        [MCPServerConfig(name="loki", command="npx")],
        allowed=frozenset({"q"}),
    )

    run = Harness(config=CONFIG, instructions="i", mcp_servers=servers)

    assert [name_of(s) for s in run.tool_servers] == ["loki"]


async def test_this_is_the_only_module_that_imports_the_sdk():
    """Ticket 23's guarantee, carried onto Pydantic AI (ticket 05). Replacing
    the SDK is a rewrite of the harness, and that is only true while nothing in
    *production* code reaches past it — declaring a tool or a toolset pulls the
    library in. `friday/sdk/testing/` is the one exception: the test-double
    seam, whose whole job is to be the single place a *test* names the vendor.
    `mcp.py` and `llm_log.py` take the vendor's names through `harness.py`, so
    they are not importers."""
    import subprocess

    allowed = {"friday/kernel/harness/harness.py", "friday/sdk/testing/__init__.py"}
    hits = subprocess.run(
        ["grep", "-rlE", r"^\s*(from|import)\s+(pydantic_ai|fastmcp|agents)\b", "friday/"],
        capture_output=True, text=True,
    ).stdout.split()

    assert set(hits) <= allowed, f"unexpected importer: {set(hits) - allowed}"


async def test_run_accepts_a_rendered_section():
    """Ticket 27 widens the seam: a bundle's rendered string is the prompt,
    and nothing else about the call changes."""
    from friday.kernel.harness.instruction_prompt import task

    h = harness([assistant_message("done")])
    result = await h.run(task("classify", None, None).render())
    assert result.output == "done"


async def test_run_still_accepts_a_plain_string():
    """The scripted-test seam: a plain string keeps working unchanged."""
    h = harness([assistant_message("done")])
    result = await h.run("plain prompt")
    assert result.output == "done"


def test_the_tool_context_alias_keeps_ctx_out_of_the_model_s_schema():
    """A tool's first parameter must be the run context and not a field the
    model has to fill, and `harness.ToolContext` is what says so.

    Pydantic AI hides a `RunContext`-typed first parameter from the tool's JSON
    schema by design; `ToolContext` is an alias of it, so the schema the model
    sees holds only the tool's own arguments. What breaks this: aliasing to
    something that is not the run context, or an SDK that stops recognising it.
    """
    # `ToolContext` and `tool` are imported at module scope on purpose:
    # `from __future__ import annotations` stringifies the signature below, and
    # the SDK resolves it with `get_type_hints` against *this module's* globals.

    @tool
    def probe(ctx: ToolContext[object], x: str) -> str:
        """Doc.

        Args:
            x: a thing.
        """
        return x

    assert set(probe.function_schema.json_schema["properties"]) == {"x"}


async def test_every_call_reaches_the_sink_it_was_built_with():
    """D1: the recording seam is handed over once, at construction.

    It was a `calls=` list on `run()`, and three of the four callers forgot
    it — the extractors, the summariser, and the responder through the pool —
    so `model_calls` held triage and nothing else while the board said it held
    every prompt. A seam a caller can forget is one that will be forgotten.
    """
    recorded: list = []

    async def sink(call) -> None:
        recorded.append(call)

    await harness([assistant_message("done")], record=sink).run("classify this")

    (call,) = recorded
    assert call.agent == "an-agent"
    assert call.model == "test-model"
    assert "classify this" in call.prompt


async def test_a_model_that_never_answers_becomes_work_for_a_person():
    """A run is bounded here, and nowhere else.

    A timeout is a failure like any other: `None` back, a reason in
    `last_error`, no exception for a caller to catch. `asyncio.TimeoutError`
    has an empty `str()`, so the reason is written rather than repeated.
    """
    from dataclasses import replace as _replace

    async def never(messages, info):
        await asyncio.sleep(30)

    run = Harness(
        config=_replace(CONFIG, timeout_seconds=0.05),
        instructions="i",
        model=FunctionModel(never, model_name="test-model"),
    )

    assert await run.run("go") is None
    assert run.last_error == "no answer within 0.05s"


async def test_a_sink_that_fails_costs_a_row_and_not_the_answer():
    """Recording runs after the expensive part is already done. A failed write
    loses a row; a raised write would lose an answer the provider has already
    been paid for — and would do it inside a `finally`."""
    async def sink(call) -> None:
        raise RuntimeError("the database is locked")

    result = await harness([assistant_message("done")], record=sink).run("go")

    assert result.output == "done"


async def test_a_call_that_never_came_back_is_still_written_down():
    """The run that times out is the one whose prompt somebody needs.

    A timeout cancels the run before the response arrives, so the ordinary
    record path never fires — but what was known at send time (the system
    prompt and the input) answers "what did we ask it?", which is the question.
    What is not known is written as absent: no output, no usage.
    """
    from dataclasses import replace as _replace

    recorded: list = []

    async def sink(call) -> None:
        recorded.append(call)

    async def never(messages, info):
        await asyncio.sleep(30)

    run = Harness(
        config=_replace(CONFIG, timeout_seconds=0.05),
        instructions="you decide what a message is",
        model=FunctionModel(never, model_name="test-model"),
        record=sink,
    )

    assert await run.run("classify this", message_id="m1") is None

    (call,) = recorded
    assert call.message_id == "m1"
    assert "classify this" in call.prompt
    assert "you decide what a message is" in call.system_prompt
    assert call.output == ""
    assert call.input_tokens == 0


async def test_a_cancelled_run_stops_rather_than_finishing_its_writes():
    """Recording must not outlive the cancellation that stopped it.

    `_write_down` awaits inside a `finally`, so a cancel delivered while a
    write is in flight raises `CancelledError` there. It is not caught, the
    loop stops, and the remaining rows are lost — the intended trade, because
    a process that keeps writing through its own shutdown is the worse failure.
    """
    written: list = []
    started = asyncio.Event()

    async def slow_sink(call) -> None:
        started.set()
        await asyncio.sleep(5)
        written.append(call)

    run = harness([assistant_message("done")], record=slow_sink)
    task = asyncio.create_task(run.run("go"))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert written == []


async def test_an_agent_that_has_spent_its_day_is_not_called_again():
    """The ceiling is checked before the money is gone, which is the one thing
    a hook cannot do. A breach is a refusal, not a truncation: it returns
    nothing and says why, and the caller's own machinery turns that into work.
    """
    from dataclasses import replace as _replace

    async def spent(agent: str) -> int:
        return 12_000

    run = Harness(
        config=_replace(CONFIG, daily_token_budget=10_000),
        instructions="i",
        model=_raising(AssertionError("the provider was called anyway")),
        spent=spent,
    )

    assert await run.run("go") is None
    assert run.last_error == "an-agent has spent 12000 of its 10000 tokens today"


async def test_an_agent_under_its_ceiling_is_left_alone():
    """The ceiling is opt-in and the measurement is not: an agent with no
    budget configured is never asked what it has spent."""
    from dataclasses import replace as _replace

    asked: list = []

    async def spent(agent: str) -> int:
        asked.append(agent)
        return 1

    with_budget = harness(
        [assistant_message("done")],
        config=_replace(CONFIG, daily_token_budget=10_000),
        spent=spent,
    )
    assert (await with_budget.run("go")).output == "done"
    assert asked == ["an-agent"]

    without = harness([assistant_message("done")], spent=spent)
    assert (await without.run("go")).output == "done"
    assert asked == ["an-agent"], "no budget, no question"


async def test_a_ledger_that_cannot_be_read_does_not_stop_the_work():
    """No exception escapes the harness — including from the budget check. It
    fails *open*: a store that cannot answer "what has this spent" is a store
    that cannot answer anything, so refusing on it would turn a transient read
    error into every agent refusing at once."""
    from dataclasses import replace as _replace

    async def unreadable(agent: str) -> int:
        raise RuntimeError("database is locked")

    run = harness(
        [assistant_message("done")],
        config=_replace(CONFIG, daily_token_budget=10),
        spent=unreadable,
    )

    assert (await run.run("go")).output == "done"


async def test_the_ceiling_is_reached_at_it_and_not_past_it():
    """On the boundary, because that is where a limit is decided."""
    from dataclasses import replace as _replace

    async def spent_exactly(agent: str) -> int:
        return 10_000

    at_it = harness(
        [assistant_message("done")],
        config=_replace(CONFIG, daily_token_budget=10_000),
        spent=spent_exactly,
    )
    assert await at_it.run("go") is None

    async def one_short(agent: str) -> int:
        return 9_999

    under = harness(
        [assistant_message("done")],
        config=_replace(CONFIG, daily_token_budget=10_000),
        spent=one_short,
    )
    assert (await under.run("go")).output == "done"


def _flaky(*failures):
    """A model that raises the given things, in order, then answers 'done'."""
    queue = list(failures)

    def fn(messages, info):
        if queue:
            raise queue.pop(0)
        return ModelResponse(parts=[TextPart("done")])

    return _Counting(fn)


class _Answered:
    """The least a provider error needs to exist. Built by hand rather than with
    the HTTP library, whose private vendored shapes a test should not break on.
    """

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.request = None
        self.headers: dict = {}


def _rate_limited():
    from openai import RateLimitError

    return RateLimitError("429 slow down", response=_Answered(429), body=None)


def _rejected():
    from openai import BadRequestError

    return BadRequestError(
        "400 that prompt is not acceptable", response=_Answered(400), body=None
    )


async def test_a_hiccup_is_tried_again_and_then_answers():
    """A 429 during a burst turned the expensive call — the model request — into
    a task a person has to pick up and that will never retry itself. Both
    attempts are recorded, because the provider billed for both."""
    from dataclasses import replace as _replace

    recorded: list = []

    async def sink(call) -> None:
        recorded.append(call)

    model = _flaky(_rate_limited())
    run = Harness(
        config=_replace(CONFIG, max_attempts=3, retry_backoff_seconds=0.0),
        instructions="i",
        model=model,
        record=sink,
    )

    assert (await run.run("go")).output == "done"
    assert model.calls == 2
    assert [c.attempt for c in recorded] == [1, 2]
    assert recorded[0].output == "", "the attempt that failed has no answer"
    assert recorded[1].output


async def test_a_prompt_the_provider_rejects_is_not_paid_for_twice():
    """Terminal on the first attempt. What counts as transient is a list, not a
    guess from the message — a 400 is the provider saying the request itself is
    wrong, which trying again cannot change."""
    from dataclasses import replace as _replace

    recorded: list = []

    async def sink(call) -> None:
        recorded.append(call)

    model = _flaky(_rejected(), _rejected(), _rejected())
    run = Harness(
        config=_replace(CONFIG, max_attempts=3, retry_backoff_seconds=0.0),
        instructions="i",
        model=model,
        record=sink,
    )

    assert await run.run("go") is None
    assert model.calls == 1
    assert "not acceptable" in run.last_error
    assert "gave up" not in run.last_error
    assert [c.attempt for c in recorded] == [1], "one call, one row"


async def test_giving_up_says_it_gave_up():
    """A person reads this. "429 slow down" alone reads as a moment; "gave up
    after 3 attempts" says the moment lasted."""
    from dataclasses import replace as _replace

    recorded: list = []

    async def sink(call) -> None:
        recorded.append(call)

    run = Harness(
        config=_replace(CONFIG, max_attempts=3, retry_backoff_seconds=0.0),
        instructions="i",
        model=_flaky(_rate_limited(), _rate_limited(), _rate_limited()),
        record=sink,
    )

    assert await run.run("go") is None
    assert run.last_error.startswith("gave up after 3 attempts:")
    assert "429" in run.last_error
    assert [c.attempt for c in recorded] == [1, 2, 3]


async def test_a_408_is_a_hiccup_and_not_a_verdict():
    """The status the SDK has no class for, and the one that matters. Every
    status at or above 500 arrives as `InternalServerError`; 408 Request Timeout
    falls through as a bare `APIStatusError` and is the opposite of a verdict —
    the request did not arrive in time, which is worth asking again."""
    from dataclasses import replace as _replace

    from openai import APIStatusError

    model = _flaky(APIStatusError("408 too slow", response=_Answered(408), body=None))
    run = Harness(
        config=_replace(CONFIG, max_attempts=3, retry_backoff_seconds=0.0),
        instructions="i",
        model=model,
    )

    assert (await run.run("go")).output == "done"
    assert model.calls == 2


def test_one_request_may_not_spend_the_whole_run():
    """`APITimeoutError` is on the list of what to retry, and it could not fire:
    the client and the run were given the same number, so the run-level timer
    always tripped first — and it cancels, which is a `BaseException` the retry
    loop never sees. A share each makes the claim true."""
    from friday.kernel.harness.harness import _chat_model
    from friday.kernel.config import AgentConfig

    model = _chat_model(
        AgentConfig(
            name="a", api_key="k", base_url="https://example.invalid/v1",
            model="test-model", timeout_seconds=60.0, max_attempts=3,
        )
    )

    assert model.client.timeout == 20.0


async def test_what_an_agent_reached_for_is_written_down_too():
    """A prompt says what an agent was asked; it does not say what it did. Which
    of the four skill tools an agent reaches for is an empirical question, and
    an expensive one the day `mcp_servers` is not empty. Through the same sink
    as the model calls, because a second seam is a second thing to forget."""
    from friday.kernel.harness.harness import tool

    written: list = []

    async def sink(entry) -> None:
        written.append(entry)

    @tool
    def look_up(name: str) -> str:
        """Find a thing.

        Args:
            name: which thing.
        """
        return "found it"

    run = harness(
        [function_call("look_up", {"name": "deploy"}, call_id="1")],
        [assistant_message("done")],
        tools=[look_up],
        record=sink,
    )
    await run.run("go", extra_turns=2, task_id=7)

    (reached,) = [e for e in written if getattr(e, "tool", None)]
    assert reached.tool == "look_up"
    assert "deploy" in reached.arguments
    assert reached.result == "found it"
    assert reached.failed is False
    assert reached.task_id == 7
    assert reached.latency_ms is not None


async def test_a_tool_that_failed_is_recorded_as_having_failed():
    """A tool that raises does not reach the model as an error it should retry.
    The run's hooks turn it into "unavailable, carry on" — a perfectly ordinary
    result — and record it as failed, using the tool call's own id. The model is
    told less than the log: the real error names a filesystem path, which is
    not the model's to see."""
    from friday.kernel.harness.harness import tool

    written: list = []

    async def sink(entry) -> None:
        written.append(entry)

    @tool
    def explodes(name: str) -> str:
        """Raises.

        Args:
            name: anything.
        """
        raise RuntimeError("the skill file is not there")

    run = harness(
        [function_call("explodes", {"name": "deploy"}, call_id="1")],
        [assistant_message("done")],
        tools=[explodes],
        record=sink,
    )
    await run.run("go", extra_turns=2)

    (reached,) = [e for e in written if getattr(e, "tool", None)]
    assert reached.failed is True
    assert "unavailable" in reached.result
    assert "skill file" not in reached.result, "the model was told less"


# --- skills are the harness's, not each agent's (operator, 2026-09-07) -------


class _Library:
    """A skill library the size of its catalogue."""

    def __init__(self, *names):
        self._names = list(names)

    def __len__(self):
        return len(self._names)

    def catalogue(self):
        return [f"{n}: what {n} does" for n in self._names]


def _config():
    from friday.kernel.config import AgentConfig

    return AgentConfig(
        name="any", api_key="k", base_url="https://example.invalid/v1", model="m"
    )


def test_a_harness_given_skills_wires_the_four_tools_itself():
    """Every agent that could reach a skill had to remember to wire four tools,
    and the operator's point is that a thing every agent needs is the harness's
    job. Forgetting it is invisible: the agent simply never reaches for
    anything, which reads as a model that did not think to."""
    agent = Harness(config=_config(), instructions="x", skills=_Library("trace"))

    named = {t.name for t in agent.tools}
    assert named == {
        "fetch_skill",
        "search_skills",
        "describe_skill",
        "read_skill_file",
    }


def test_a_harness_with_an_empty_library_is_told_about_no_skills():
    """An empty catalogue and no tools are the same fact. An agent told about a
    door that is not in the room goes looking for it."""
    assert Harness(config=_config(), instructions="x", skills=_Library()).tools == []
    assert Harness(config=_config(), instructions="x").tools == []


def test_the_catalogue_is_readable_off_the_harness():
    """The prompt needs the catalogue and the tools need the library; both come
    from one place so the two cannot describe different skills."""
    agent = Harness(config=_config(), instructions="x", skills=_Library("a", "b"))

    assert agent.skills == ["a: what a does", "b: what b does"]
    assert Harness(config=_config(), instructions="x").skills == []


def test_skill_tools_do_not_eat_the_turn_that_answers():
    """Every agent here is `max_turns: 1`. A skill tool spends a turn, so without
    room for it a `fetch_skill` consumes the only turn and the agent never
    classifies. The harness that hands out the tools also hands out the turns."""
    bare = Harness(config=_config(), instructions="x")
    withskills = Harness(config=_config(), instructions="x", skills=_Library("a"))

    assert withskills.tool_turns > bare.tool_turns


def test_the_two_step_reach_for_a_skill_fits_in_the_budget():
    """The catalogue names a skill in a line, so an agent that recognises it
    calls `fetch_skill` and answers (one tool turn). An agent that does not is
    told to `search_skills` first and *then* fetch (two)."""
    withskills = Harness(config=_config(), instructions="x", skills=_Library("a"))

    assert withskills.tool_turns >= 2


def test_run_owns_the_turns_for_its_own_tools():
    """The harness wires the skill tools; the caller passes `extra_turns` for its
    own. The caller must not have to remember the harness's. Deleting the
    addition from `run` flips this red."""
    budgeted = Harness(config=_config(), instructions="x", skills=_Library("a"))
    bare = Harness(config=_config(), instructions="x")

    asked: list[int] = []

    async def fake_settle(*_a, max_turns: int, **_kw) -> None:
        asked.append(max_turns)

    budgeted._settle = fake_settle  # type: ignore[assignment]
    bare._settle = fake_settle  # type: ignore[assignment]

    asyncio.run(budgeted.run("p"))
    asyncio.run(bare.run("p"))
    # 1 from max_turns, +1 tool turn for the harness that wired skill tools.
    assert asked == [3, 1]


def test_an_agent_can_declare_both_a_shape_and_its_own_settings():
    """`answers=` forces its output tool (Pydantic AI forces it when no text
    output is allowed), and `config.yaml`'s `settings:` block may carry its own
    knobs. These reached `ModelSettings` as two splats side by side —
    `**config.settings, **model_settings` — which is a `TypeError: got multiple
    values` the moment a key appears in both. Merged instead, and a stray
    `tool_choice` is dropped for a forced-output agent, because it would fight
    the forcing rather than help it."""
    from dataclasses import dataclass

    @dataclass
    class Shape:
        value: str = ""

    built = Harness(
        config=AgentConfig(
            name="both", api_key="k", base_url="http://x/v1", model="m",
            settings={"tool_choice": "auto", "max_tokens": 64},
        ),
        instructions="i",
        answers=Shape,
        model=ScriptedModel([]),
    )

    assert built.agent.model_settings.get("tool_choice") is None
    assert built.agent.model_settings["max_tokens"] == 64, (
        "the rest of the configured settings survived the merge"
    )


async def test_a_run_carrying_state_does_not_have_to_name_its_own_message():
    """D8: the recording sink reads the message and the task off the run's
    state. `node` stays explicit, because it is not a fact about the message."""
    from friday.kernel.domain.models import FridayState

    written: list = []

    async def sink(call):
        written.append(call)

    h = Harness(
        config=AgentConfig(name="a", api_key="k", base_url="http://x/v1", model="m"),
        instructions="i",
        model=ScriptedModel([[assistant_message("ok")]]),
        record=sink,
    )

    await h.run(
        "ask",
        context=FridayState(channel_id="c1", agent="a", message_id="m1").for_task(7),
        node="prepare",
    )

    (call,) = written
    assert (call.message_id, call.task_id, call.node) == ("m1", 7, "prepare")


async def test_a_caller_that_knows_better_than_its_state_still_wins():
    """A run about a different message than the one the state carries."""
    from friday.kernel.domain.models import FridayState

    written: list = []

    async def sink(call):
        written.append(call)

    h = Harness(
        config=AgentConfig(name="a", api_key="k", base_url="http://x/v1", model="m"),
        instructions="i",
        model=ScriptedModel([[assistant_message("ok")]]),
        record=sink,
    )

    await h.run(
        "ask",
        context=FridayState(channel_id="c1", agent="a", message_id="m1"),
        message_id="m9",
    )

    assert written[0].message_id == "m9"


def test_an_agent_with_a_shape_may_not_have_its_mechanism_overridden():
    """Three things an `answers=` agent owns, and a caller may override none: the
    output tool (its `output_type`), the one correction (`retries`), and the
    forcing (a `tool_choice` in `model_settings` other than the output tool
    would disable it). A caller passing any is asking for something that cannot
    work, so it is refused rather than quietly honoured."""
    from dataclasses import dataclass

    @dataclass
    class Shape:
        value: str = ""

    def build(**options):
        return Harness(
            config=AgentConfig(
                name="owned", api_key="k", base_url="http://x/v1", model="m"
            ),
            instructions="i",
            answers=Shape,
            model=ScriptedModel([]),
            **options,
        )

    with pytest.raises(ValueError, match="tool_choice"):
        build(model_settings={"tool_choice": "auto"})

    with pytest.raises(ValueError, match="retries"):
        build(retries={"output": 5})

    # An agent that declares neither is untouched.
    assert build(model_settings={"max_tokens": 64}).agent.model_settings["max_tokens"] == 64


async def test_a_terminal_tool_finishes_the_run_beside_the_answer():
    """`ends_with` adds a second output tool: calling it ends the run with its
    own return, the way the answer tool ends it with the answer shape. The
    diagnose loop offers `hand_over(reason)` this way, so the model can escalate
    instead of answering."""
    handed = await harness(
        [function_call("_escalate", {"reason": "beyond me"})],
        answers=_ReplyShape,
        ends_with=[_escalate],
    ).run_structured("go")

    assert handed == _Escalated("beyond me")


async def test_the_answer_shape_still_wins_when_the_model_answers():
    """The terminal tool is a *second* way to finish, not a replacement: a model
    that calls the answer tool still comes back as the answer shape."""
    answered = await harness(
        [function_call("answer", {"value": "done"})],
        answers=_ReplyShape,
        ends_with=[_escalate],
    ).run_structured("go")

    assert answered == _ReplyShape(value="done")


def test_a_terminal_tool_without_a_resolvable_return_is_refused():
    """A terminal tool whose return type cannot be read would be callable by the
    model but unrecognised by `run_structured` — the outcome would be silently
    lost as 'no answer'. Refuse it at build time, where the message names it."""
    def escalate(reason):  # no return annotation
        return _Escalated(reason)

    with pytest.raises(ValueError, match="return annotation"):
        Harness(
            config=CONFIG, instructions="i", model=ScriptedModel([]),
            answers=_ReplyShape, ends_with=[escalate],
        )


def test_a_terminal_tool_whose_name_collides_is_refused():
    """The answer tool is named 'answer'; a terminal tool may not reuse that
    name (nor another terminal's) — duplicate output-tool names are undefined in
    Pydantic AI, so the collision is caught at build time."""
    from friday.sdk.tools import tool as sdk_tool

    clashing = sdk_tool(name="answer")(_escalate)  # a terminal named 'answer'

    with pytest.raises(ValueError, match="collides"):
        Harness(
            config=CONFIG, instructions="i", model=ScriptedModel([]),
            answers=_ReplyShape, ends_with=[clashing],
        )


def test_a_terminal_tool_without_an_answer_shape_is_refused():
    """`ends_with` is a finish *beside* an answer; with no `answers=` there is
    nothing for it to sit beside, and Pydantic AI's forced-output mechanism the
    terminal tools ride on is not set up."""
    with pytest.raises(ValueError, match="answers"):
        Harness(
            config=CONFIG, instructions="i",
            model=ScriptedModel([]), ends_with=[_escalate],
        )


class _Slow(FunctionModel):
    """Answers every call with the prompt it was given, after a pause long
    enough for a second run of the same harness to start meanwhile."""

    def __init__(self) -> None:
        async def reply(messages, info):
            await asyncio.sleep(0.02)
            return ModelResponse(parts=[TextPart(_user_text(messages))])

        super().__init__(reply, model_name="test-model")


async def test_two_runs_of_one_harness_each_write_down_their_own_call():
    """The pool works tasks side by side (ticket 13), and one extractor — one
    responder — serves every task of its kind, so two runs of one harness at
    once is the ordinary case now. The hooks that build a run's `ModelCall` are
    handed to the run through `capabilities=`, per run — the shared `agent.hooks`
    this replaced had a second run overwrite the first's, so the first's call was
    written down under the second's task."""
    recorded: list = []

    async def sink(call) -> None:
        recorded.append(call)

    run = Harness(config=CONFIG, instructions="i", model=_Slow(), record=sink)

    first, second = await asyncio.gather(
        run.run("about task one", task_id=1),
        run.run("about task two", task_id=2),
    )

    assert "task one" in first.output
    assert "task two" in second.output
    assert sorted(c.task_id for c in recorded) == [1, 2]
    for call in recorded:
        expected = "task one" if call.task_id == 1 else "task two"
        assert expected in call.prompt, (call.task_id, call.prompt)


async def test_why_a_run_failed_survives_a_second_run_starting():
    """`last_error`, `refusal` and `unfit` live on the harness and are read by
    the caller the moment the run returns. A second run of the same harness
    clears them as it begins — so one that began while the first was still
    writing its record down wiped the first's reason."""
    from dataclasses import replace as _replace

    async def slow_sink(call) -> None:
        await asyncio.sleep(0.05)

    run = Harness(
        config=_replace(CONFIG, max_attempts=1),
        instructions="i",
        model=_flaky(_rejected()),
        record=slow_sink,
    )

    async def first():
        said = await run.run("go")
        return said, run.last_error

    async def second():
        await asyncio.sleep(0.01)
        return await run.run("go again")

    (said, why), _ = await asyncio.gather(first(), second())

    assert said is None
    assert why is not None and "not acceptable" in why
