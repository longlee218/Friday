"""Ticket 14 — one place an agent is run.

Everything every agent needs and none of them should restate: the client, the
settings, the logging hooks, the caps, and the rule that a failure becomes work
for a person rather than an exception nobody catches.

Written after the second agent existed, not before. One agent is a hypothetical
seam; building this against triage alone would have been guessing at what
varies.
"""

from __future__ import annotations

import pytest
from agents import Agent
from agents.models.interface import Model
from agents.testing import ScriptedModel, assistant_message

from friday.config import AgentConfig
from friday.agent.harness import Harness, ToolContext, tool

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


async def test_it_runs_an_agent_and_hands_back_the_result():
    result = await harness([assistant_message("done")]).run("go")

    assert result.final_output == "done"


async def test_a_failure_is_no_result_rather_than_an_exception():
    """Every agent turns this into its own kind of work — a task for a human,
    or a fall back to a template. None of them should have to catch it."""

    class Broken(Model):
        async def get_response(self, *a, **kw):
            raise RuntimeError("provider down")

        def stream_response(self, *a, **kw):
            raise NotImplementedError

    result = await Harness(
        config=CONFIG, instructions="do the thing", model=Broken()
    ).run("go")

    assert result is None


async def test_the_reason_it_failed_is_kept():
    class Broken(Model):
        async def get_response(self, *a, **kw):
            raise RuntimeError("provider down")

        def stream_response(self, *a, **kw):
            raise NotImplementedError

    run = Harness(config=CONFIG, instructions="do the thing", model=Broken())
    await run.run("go")

    assert "provider down" in run.last_error


async def test_a_credential_never_appears_in_the_reason():
    """The reason is stored against a task, and a provider exception can quote
    an Authorization header."""

    class Leaking(Model):
        async def get_response(self, *a, **kw):
            raise RuntimeError("401 for Bearer sk-abcdefghijklmnopqrstuvwx")

        def stream_response(self, *a, **kw):
            raise NotImplementedError

    run = Harness(config=CONFIG, instructions="i", model=Leaking())
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
    assert built.agent.model.model == "test-model"
    assert built.agent.model_settings.temperature == 0


async def test_tracing_is_off():
    """It exports to OpenAI using the same key as model requests, which with a
    third-party provider leaks both the traffic and the credential."""
    from agents.tracing import get_trace_provider

    Harness(config=CONFIG, instructions="i")

    assert get_trace_provider()._disabled


async def test_an_agent_can_be_given_servers_it_did_not_have_to_know_about():
    """Which servers an agent gets is composition, not something it declares —
    the same reason its model and base_url are configuration."""
    from friday.config import MCPServerConfig
    from friday.agent.mcp import build

    servers = build([MCPServerConfig(name="loki", command="npx", allow=("q",))])

    run = Harness(config=CONFIG, instructions="i", mcp_servers=servers)

    assert [s.name for s in run.agent.mcp_servers] == ["loki"]


async def test_this_is_the_only_module_that_imports_the_sdk():
    """Ticket 23's guarantee. Replacing the SDK is a rewrite of this file, and
    that is only true while nothing else reaches past it — declaring a tool
    pulls the library in, so a second importer would spread the dependency to
    every agent written after it."""
    import subprocess

    allowed = {"friday/agent/harness.py"}
    hits = subprocess.run(
        ["grep", "-rlE", r"^\s*(from agents|import agents)\b", "friday/"],
        capture_output=True, text=True,
    ).stdout.split()

    assert set(hits) <= allowed, f"unexpected importer: {set(hits) - allowed}"


async def test_run_accepts_a_rendered_section():
    """Ticket 27 widens the seam: a bundle's rendered string is the prompt,
    and nothing else about the call changes."""
    from agents.testing import ScriptedModel, assistant_message
    from friday.agent.instruction_prompt import task

    h = harness([assistant_message("done")])
    result = await h.run(task("classify", None, None).render())
    assert result.final_output == "done"


async def test_run_still_accepts_a_plain_string():
    """The scripted-test seam: a plain string keeps working unchanged."""
    from agents.testing import ScriptedModel, assistant_message

    h = harness([assistant_message("done")])
    result = await h.run("plain prompt")
    assert result.final_output == "done"


# --- checkpoint / resume (ticket 07) ----------------------------------------


async def test_a_tool_marked_needs_approval_interrupts_rather_than_running():
    """The whole reason this exists: the run stops at the call, the tool's
    own body never executes, and `result.interruptions` says why."""
    from agents.testing import function_call
    from friday.agent.harness import tool

    ran: list[str] = []

    @tool(needs_approval=True)
    def apply_fix(diff: str) -> str:
        ran.append(diff)
        return "applied"

    h = harness(
        [function_call("apply_fix", {"diff": "a diff"}, call_id="1")],
        tools=[apply_fix],
    )

    result = await h.run("fix it")

    assert result is not None
    assert len(result.interruptions) == 1
    assert ran == [], "the tool ran before anyone approved it"


async def test_checkpoint_and_resume_round_trip_through_json_and_a_fresh_agent():
    """`checkpoint`'s output has to survive the trip a real approval takes:
    written to a database row, read back in a different process — rebuilt
    against a *freshly constructed* Harness, not the one that paused."""
    import json

    from agents.testing import ScriptedModel, assistant_message, function_call
    from friday.agent.harness import tool

    ran: list[str] = []

    @tool(needs_approval=True)
    def apply_fix(diff: str) -> str:
        ran.append(diff)
        return "applied"

    first = harness(
        [function_call("apply_fix", {"diff": "a diff"}, call_id="1")],
        tools=[apply_fix],
    )
    paused = await first.run("fix it", extra_turns=2)
    blob = first.checkpoint(paused)
    json.dumps(blob)  # must actually be JSON-serialisable, not just dict-shaped

    second = Harness(
        config=CONFIG,
        instructions="do the thing",
        tools=[apply_fix],
        model=ScriptedModel([[assistant_message("done")]]),
    )
    resumed = await second.resume(blob)

    assert ran == ["a diff"], "approval must let the tool actually run"
    assert resumed.final_output == "done"


async def test_a_second_needs_approval_call_is_still_an_interruption_on_resume():
    """Resuming does not assume the model behaves — if it asks for approval
    again, that has to come back as another interruption, not a crash."""
    from agents.testing import function_call
    from friday.agent.harness import tool

    @tool(needs_approval=True)
    def apply_fix(diff: str) -> str:
        return "applied"

    first = harness(
        [function_call("apply_fix", {"diff": "a diff"}, call_id="1")],
        tools=[apply_fix],
    )
    paused = await first.run("fix it", extra_turns=2)
    blob = first.checkpoint(paused)

    second = Harness(
        config=CONFIG,
        instructions="do the thing",
        tools=[apply_fix],
        model=ScriptedModel(
            [[function_call("apply_fix", {"diff": "a second diff"}, call_id="2")]]
        ),
    )
    resumed = await second.resume(blob)

    assert resumed is not None
    assert len(resumed.interruptions) == 1


async def test_a_run_paused_on_two_approvals_becomes_work_rather_than_an_exception():
    """Ticket 12. `resume` unpacked the pending approvals into a single name,
    so a model that emitted two `apply_fix` calls in one turn — ordinary
    parallel tool calling, nothing exotic — raised `ValueError` straight past
    every caller.

    That unpack sat outside the try/except the rest of this module lives by,
    so the one rule the harness exists to enforce did not apply to it: a
    failure is `None` and a `last_error`, which the caller turns into work for
    a person. It is not an exception nobody catches, leaving the task wedged
    with its approval row intact and no way to clear it.
    """
    from agents.testing import function_call
    from friday.agent.harness import tool

    ran: list[str] = []

    @tool(needs_approval=True)
    def apply_fix(diff: str) -> str:
        ran.append(diff)
        return "applied"

    paused = harness(
        [
            function_call("apply_fix", {"diff": "one"}, call_id="1"),
            function_call("apply_fix", {"diff": "two"}, call_id="2"),
        ],
        tools=[apply_fix],
    )
    result = await paused.run("fix it", extra_turns=2)
    assert len(result.interruptions) == 2, "the premise: two at once"

    resumed = harness([], tools=[apply_fix])

    outcome = await resumed.resume(paused.checkpoint(result))

    assert outcome is None, "a failure is None, not a raise"
    assert resumed.last_error, "and it says why, for the task it becomes"
    assert ran == [], "nothing was approved, so nothing ran"


def test_the_tool_context_alias_keeps_ctx_out_of_the_model_s_schema():
    """A tool's first parameter must be the run context and not a field the
    model has to fill, and `harness.ToolContext` is what says so.

    Asserted as the outcome rather than as the alias's identity. The first
    version of this test checked `harness.ToolContext in (RunContextWrapper,
    SdkToolContext)` and argued, in its own docstring, that the SDK decides
    this by identity rather than `issubclass`. That premise is true —
    `function_schema.py` uses `is`, twice — but the assertion never went near
    `function_schema`: it compared two names the test imported itself, so it
    would have stayed green if the SDK changed, and gone red on a subclass a
    changed SDK handled perfectly well. It argued for a property it did not
    run.

    What breaks this: aliasing to a subclass (`ctx` becomes a required string
    the model must supply), deleting the alias, or an SDK that stops
    recognising whatever it points at. The reason the identity check makes a
    subclass unsafe belongs in `harness.py`, next to the alias, and is there.
    """
    # `ToolContext` and `tool` are imported at module scope on purpose:
    # `from __future__ import annotations` stringifies the signature below, and
    # the SDK resolves it with `get_type_hints` against *this module's*
    # globals. A function-local import leaves it a name nothing can resolve —
    # the same trap `friday/extraction/answer.py` documents for the
    # `Literal` it generates.

    @tool
    def probe(ctx: ToolContext[object], x: str) -> str:
        """Doc.

        Args:
            x: a thing.
        """
        return x

    assert set(probe.params_json_schema["properties"]) == {"x"}


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

    The OpenAI client defaults to ten minutes and retries past that, and the
    pool works one task at a time — so an unbounded run does not stall one
    task, it stalls every task, while the heartbeat goes on reporting the
    process alive. The bound is per agent and configured, because a
    classification and a drafted reply are not the same wait.

    A timeout is a failure like any other: `None` back, a reason in
    `last_error`, no exception for a caller to catch. `asyncio.TimeoutError`
    has an empty `str()`, so the reason is written rather than repeated —
    without that the operator reads "triage failed: " and nothing else.
    """
    import asyncio
    from dataclasses import replace as _replace

    class NeverAnswers(Model):
        async def get_response(self, *a, **kw):
            await asyncio.sleep(30)

        def stream_response(self, *a, **kw):
            raise NotImplementedError

    run = Harness(
        config=_replace(CONFIG, timeout_seconds=0.05),
        instructions="i",
        model=NeverAnswers(),
    )

    assert await run.run("go") is None
    assert run.last_error == "no answer within 0.05s"


async def test_a_sink_that_fails_costs_a_row_and_not_the_answer():
    """Recording runs after the expensive part is already done.

    A failed write loses a row; a raised write would lose an answer the
    provider has already been paid for — and would do it inside a `finally`,
    which is where the run's own result is waiting.
    """
    async def sink(call) -> None:
        raise RuntimeError("the database is locked")

    result = await harness([assistant_message("done")], record=sink).run("go")

    assert result.final_output == "done"


async def test_a_call_that_never_came_back_is_still_written_down():
    """The run that times out is the one whose prompt somebody needs.

    `LogHooks` builds its `ModelCall` in `on_llm_end`, and a timeout cancels
    the run before that fires — so the middleware's `finally` had nothing to
    hand the sink, and the agents this matters most for are the ones it fails
    for. `max_turns: 1` is triage, all three extractors and the summariser:
    for every one of them a timeout meant *zero* rows, and the prompt of the
    call that hung is exactly what was missing.

    What is known at `on_llm_start` — the system prompt and the input — is
    enough to answer "what did we ask it?", which is the question. What is not
    known is written as absent rather than as zero: no output, no usage.
    """
    import asyncio
    from dataclasses import replace as _replace

    recorded: list = []

    async def sink(call) -> None:
        recorded.append(call)

    class NeverAnswers(Model):
        async def get_response(self, *a, **kw):
            await asyncio.sleep(30)

        def stream_response(self, *a, **kw):
            raise NotImplementedError

    run = Harness(
        config=_replace(CONFIG, timeout_seconds=0.05),
        instructions="you decide what a message is",
        model=NeverAnswers(),
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
    write is in flight raises `CancelledError` there. It is not caught —
    `except Exception` does not reach it — the loop stops, and the remaining
    rows are lost. That is the intended trade: the caller has already stopped
    waiting for the answer those rows describe, and a process that keeps
    writing through its own shutdown is the worse failure.

    Pinned because widening that catch to `BaseException` looks like an
    improvement — "record even on cancellation" — and quietly turns Ctrl-C
    into a process that will not stop.
    """
    import asyncio

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
    a hook cannot do — `on_llm_start` fires after the decision to spend.

    A breach is a refusal, not a truncation. Every other refusal in this
    system routes to a person, and a silently shortened answer under the
    operator's name is exactly what those rules exist to prevent — so this
    returns nothing and says why, and the caller's own machinery turns that
    into work the way it turns any other non-answer.
    """
    from dataclasses import replace as _replace

    class NeverReached(Model):
        async def get_response(self, *a, **kw):
            raise AssertionError("the provider was called anyway")

        def stream_response(self, *a, **kw):
            raise NotImplementedError

    async def spent(agent: str) -> int:
        return 12_000

    run = Harness(
        config=_replace(CONFIG, daily_token_budget=10_000),
        instructions="i",
        model=NeverReached(),
        spent=spent,
    )

    assert await run.run("go") is None
    assert run.last_error == "an-agent has spent 12000 of its 10000 tokens today"


async def test_an_agent_under_its_ceiling_is_left_alone():
    """The ceiling is opt-in and the measurement is not: an agent with no
    budget configured is never asked what it has spent, which is what keeps
    this from being a query on the hot path of every call for installs that
    have not set one."""
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
    assert (await with_budget.run("go")).final_output == "done"
    assert asked == ["an-agent"]

    without = harness([assistant_message("done")], spent=spent)
    assert (await without.run("go")).final_output == "done"
    assert asked == ["an-agent"], "no budget, no question"


async def test_a_ledger_that_cannot_be_read_does_not_stop_the_work():
    """No exception escapes the harness — including from the budget check.

    The check runs before the run and so outside the clause that catches
    everything else, which is how it came to be the one path that could raise
    past every caller. It fails *open*: a store that cannot answer "what has
    this spent" is a store that cannot answer anything, so refusing on it
    would turn a transient read error into every agent refusing at once, and
    the money the ceiling protects is not at risk from a read that failed.
    """
    from dataclasses import replace as _replace

    async def unreadable(agent: str) -> int:
        raise RuntimeError("database is locked")

    run = harness(
        [assistant_message("done")],
        config=_replace(CONFIG, daily_token_budget=10),
        spent=unreadable,
    )

    assert (await run.run("go")).final_output == "done"


async def test_the_ceiling_is_reached_at_it_and_not_past_it():
    """On the boundary, because that is where a limit is decided.

    Neither of the tests above sits on it — one is well over and one is well
    under — so `spent < budget` could have been `spent <= budget`, a whole
    budget's worth of overspend, and stayed green. A ceiling that lets you
    reach it and then spend it again is not the number anybody configured.
    """
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
    assert (await under.run("go")).final_output == "done"


def _flaky(*failures):
    """A model that raises the given things, in order, then answers."""
    from agents.items import ModelResponse
    from agents.usage import Usage
    from openai.types.responses import ResponseOutputMessage, ResponseOutputText

    queue = list(failures)

    class Flaky(Model):
        calls = 0

        async def get_response(self, *a, **kw):
            Flaky.calls += 1
            if queue:
                raise queue.pop(0)
            return ModelResponse(
                output=[
                    ResponseOutputMessage(
                        id="1", role="assistant", status="completed", type="message",
                        content=[ResponseOutputText(
                            text="done", type="output_text", annotations=[]
                        )],
                    )
                ],
                usage=Usage(requests=1, input_tokens=5, output_tokens=2),
                response_id=None,
            )

        def stream_response(self, *a, **kw):
            raise NotImplementedError

    return Flaky()


class _Answered:
    """The least a provider error needs to exist.

    Built by hand rather than with the HTTP library: the SDK vendors it under
    a private name, and a test that reaches for that is a test that breaks on
    an upgrade for a reason having nothing to do with what it checks.
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
    """The asymmetry this fixes: the outbox retries a *send*, and the layer
    one call in — the expensive one — turned a 429 during a burst into a task
    a person has to pick up and that will never retry itself.

    Both attempts are recorded, because the provider billed for both. A record
    that counts one call where the invoice counts two is not a record.
    """
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

    assert (await run.run("go")).final_output == "done"
    assert model.calls == 2
    assert [c.attempt for c in recorded] == [1, 2]
    assert recorded[0].output == "", "the attempt that failed has no answer"
    assert recorded[1].output


async def test_a_prompt_the_provider_rejects_is_not_paid_for_twice():
    """Terminal on the first attempt. What counts as transient is a list, not
    a guess from the message — and a 400 is the provider saying the request
    itself is wrong, which trying again cannot change."""
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
    after 3 attempts" says the moment lasted, which is the difference between
    something to ignore and something to look at."""
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
    # Three calls, three rows. The record over-counting the invoice is the
    # same failure as under-counting it, and the flush runs twice on every
    # path that ends in an exception — once where the attempt failed and once
    # in the `finally` — so the row it builds has to be consumed, not copied.
    assert [c.attempt for c in recorded] == [1, 2, 3]


async def test_a_408_is_a_hiccup_and_not_a_verdict():
    """The status the SDK has no class for, and the one that matters.

    Every status at or above 500 arrives as `InternalServerError`, so the
    `>= 500` branch this replaced could never fire — while 408 Request Timeout
    fell through the same branch as a bare `APIStatusError` and was treated as
    the provider's final answer. It is the opposite: the request did not
    arrive in time, which is the definition of worth asking again.
    """
    from dataclasses import replace as _replace

    from openai import APIStatusError

    model = _flaky(APIStatusError("408 too slow", response=_Answered(408), body=None))
    run = Harness(
        config=_replace(CONFIG, max_attempts=3, retry_backoff_seconds=0.0),
        instructions="i",
        model=model,
    )

    assert (await run.run("go")).final_output == "done"
    assert model.calls == 2


def test_one_request_may_not_spend_the_whole_run():
    """`APITimeoutError` is on the list of what to retry, and it could not
    fire: the client and the run were given the same number, so the run-level
    timer always tripped first — and it cancels, which is a `BaseException`
    the retry loop never sees. A hung provider burned the entire budget on one
    attempt and reported "no answer within 60s", never "gave up after 3".

    A share each makes the claim true. The cost is that one slow-but-working
    call now fails where it used to be waited out, which is the right way
    round for agents that send one short prompt and read one short answer.
    """
    from friday.agent.harness import _chat_model
    from friday.config import AgentConfig

    model = _chat_model(
        AgentConfig(
            name="a", api_key="k", base_url="https://example.invalid/v1",
            model="test-model", timeout_seconds=60.0, max_attempts=3,
        )
    )

    assert model._client.timeout == 20.0


async def test_what_an_agent_reached_for_is_written_down_too():
    """A prompt says what an agent was asked; it does not say what it did.

    Four of this system's twelve tools reach a skill, and which one an agent
    reaches for — the catalogue by name, or the search when the catalogue's
    wording did not surface it — is an empirical question nothing could answer.
    It becomes an expensive one the day `mcp_servers` is not empty: a tool that
    leaves this process, with arguments a model chose, and no record of what it
    was asked for.

    Through the same sink as the model calls, because a second seam is a
    second thing to forget — which is the whole of D1.
    """
    from friday.agent.harness import ToolContext, tool

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

    from agents.testing import function_call

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
    """A tool that raises does not reach the hooks as a failure.

    `harness._tool_failed` turns it into a message for the model, which is a
    perfectly ordinary *result* as far as the SDK is concerned — so a hook
    watching for raises sees nothing, and every failure would be filed as an
    answer that happens to read like one. The harness tells the hooks, using
    the `agent` and `tool_call_id` the SDK has been passing every tool all
    along.
    """
    from agents.testing import function_call

    from friday.agent.harness import tool

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
        # `catalogue()` and `__len__` are the whole of what `Harness` uses,
        # and both exist on the real `SkillLibrary`. An earlier version of
        # this stub also had a `names()` the real one does not — a fixture
        # that invents an API passes while describing itself.
        return [f"{n}: what {n} does" for n in self._names]


def _config():
    from friday.config import AgentConfig

    return AgentConfig(
        name="any", api_key="k", base_url="https://example.invalid/v1", model="m"
    )


def test_a_harness_given_skills_wires_the_four_tools_itself():
    """Every agent that could reach a skill had to remember to wire four
    tools, and the operator's point is that a thing every agent needs is the
    harness's job. Forgetting it is invisible: the agent simply never reaches
    for anything, which reads as a model that did not think to."""
    from friday.agent.harness import Harness

    agent = Harness(config=_config(), instructions="x", skills=_Library("trace"))

    named = {t.name for t in agent.agent.tools}
    assert named == {
        "fetch_skill",
        "search_skills",
        "describe_skill",
        "read_skill_file",
    }


def test_a_harness_with_an_empty_library_is_told_about_no_skills():
    """An empty catalogue and no tools are the same fact. An agent told about
    a door that is not in the room goes looking for it — the rule this
    codebase already applies to `clarification_system` and the memory tools."""
    from friday.agent.harness import Harness

    assert Harness(config=_config(), instructions="x", skills=_Library()).agent.tools == []
    assert Harness(config=_config(), instructions="x").agent.tools == []


def test_the_catalogue_is_readable_off_the_harness():
    """The prompt needs the catalogue and the tools need the library; both
    come from one place so the two cannot describe different skills."""
    from friday.agent.harness import Harness

    agent = Harness(config=_config(), instructions="x", skills=_Library("a", "b"))

    assert agent.skills == ["a: what a does", "b: what b does"]
    assert Harness(config=_config(), instructions="x").skills == []


def test_skill_tools_do_not_eat_the_turn_that_answers():
    """Every agent here is `max_turns: 1`. A skill tool spends a turn, so
    without room for it a `fetch_skill` consumes the only turn and the agent
    never classifies, never extracts, never drafts — the mention lands in
    `needs_human` and the reason is invisible.

    So the harness that hands out the tools also hands out the turns for
    them. Wiring the one without the other is worse than wiring neither."""
    from friday.agent.harness import Harness

    bare = Harness(config=_config(), instructions="x")
    withskills = Harness(config=_config(), instructions="x", skills=_Library("a"))

    assert withskills.tool_turns > bare.tool_turns


def test_the_two_step_reach_for_a_skill_fits_in_the_budget():
    """The catalogue names a skill in a line, so an agent that recognises the
    line calls `fetch_skill` and answers: one tool turn. An agent that does
    not recognise it is told to `search_skills` first and *then* fetch —
    which is the documented split, and it is two tool turns.

    A budget of one funds the first path and quietly forbids the second, so
    the tool that exists for the harder case is the one an agent can never
    afford to follow through on."""
    from friday.agent.harness import Harness

    withskills = Harness(config=_config(), instructions="x", skills=_Library("a"))

    assert withskills.tool_turns >= 2



def test_run_owns_the_turns_for_its_own_tools():
    """The harness wires the skill tools; the caller passes `extra_turns`
    for its own (memory tools on the responder, the answer-call-then-
    answer two turns on triage). The caller must not have to remember
    the harness's. A `fetch_skill` spent the only turn that classifies,
    the mention went to `needs_human` with no reason on it, and the
    answer was that every caller was once told to write `1 + tool_turns`
    and one of three did.

    So the harness adds its own. The caller's `extra_turns` is on top,
    and that is the only thing the caller has to know about. The previous
    test read this contract out of three call sites; this one reads it
    out of one, and deleting the addition from `run` flips it red."""
    import asyncio

    from friday.agent.harness import Harness

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
    # The bare harness has no skill tools and so no tool turn.
    assert asked == [3, 1]


def test_an_agent_can_declare_both_a_shape_and_its_own_tool_choice():
    """`answers=` sets `tool_choice: required`, and `config.yaml`'s `settings:`
    block can set one too. These reached `ModelSettings` as two splats side by
    side — `**config.settings, **model_settings` — which is a `TypeError: got
    multiple values` the moment a key appears in both. At construction, in a
    process that migrates and builds its agents before it serves anything, that
    is a boot loop whose only clue is a keyword name.

    **What the harness wired itself wins over the file**, which is the
    opposite of the usual direction and is deliberate for the one key that
    collides: an `answers=` agent that does not force its tool call writes
    prose instead, so a `tool_choice: auto` in `config.yaml` would quietly
    disable the mechanism the agent was built around.
    """
    from dataclasses import dataclass

    from agents.testing import ScriptedModel

    from friday.agent.harness import Harness
    from friday.config import AgentConfig

    @dataclass
    class Shape:
        value: str = ""

    harness = Harness(
        config=AgentConfig(
            name="both", api_key="k", base_url="http://x/v1", model="m",
            settings={"tool_choice": "auto", "max_tokens": 64},
        ),
        instructions="i",
        answers=Shape,
        model=ScriptedModel([]),
    )

    assert harness.agent.model_settings.tool_choice == "required"
    assert harness.agent.model_settings.max_tokens == 64, (
        "the rest of the configured settings survived the merge"
    )


async def test_a_run_carrying_state_does_not_have_to_name_its_own_message():
    """D8: the recording sink reads the message and the task off the run's
    state. The state already knows both — that is most of what it is for — so
    a caller carrying one should not have to say it again, and `Pool._say` was
    doing exactly that: building a state and unpacking `task_id` straight back
    out one line later.

    `node` stays explicit, because it is not a fact about the message. It is
    which step of a graph asked, which the graph knows and the journey does
    not.
    """
    from agents.testing import ScriptedModel, assistant_message

    from friday.agent.harness import Harness
    from friday.config import AgentConfig
    from friday.domain.models import FridayState

    written: list = []

    async def sink(call):
        written.append(call)

    harness = Harness(
        config=AgentConfig(name="a", api_key="k", base_url="http://x/v1", model="m"),
        instructions="i",
        model=ScriptedModel([[assistant_message("ok")]]),
        record=sink,
    )

    await harness.run(
        "ask",
        context=FridayState(channel_id="c1", agent="a", message_id="m1").for_task(7),
        node="prepare",
    )

    (call,) = written
    assert (call.message_id, call.task_id, call.node) == ("m1", 7, "prepare")


async def test_a_caller_that_knows_better_than_its_state_still_wins():
    """A run about a different message than the one the state carries — which
    is what `about_message` exists for on the other side of the same
    question."""
    from agents.testing import ScriptedModel, assistant_message

    from friday.agent.harness import Harness
    from friday.config import AgentConfig
    from friday.domain.models import FridayState

    written: list = []

    async def sink(call):
        written.append(call)

    harness = Harness(
        config=AgentConfig(name="a", api_key="k", base_url="http://x/v1", model="m"),
        instructions="i",
        model=ScriptedModel([[assistant_message("ok")]]),
        record=sink,
    )

    await harness.run(
        "ask",
        context=FridayState(channel_id="c1", agent="a", message_id="m1"),
        message_id="m9",
    )

    assert written[0].message_id == "m9"


def test_an_agent_with_a_shape_may_not_have_its_mechanism_overridden():
    """Two settings an `answers=` agent owns, and a caller may override
    neither: the terminator is what knows that "answered" means an instance
    rather than any tool output, and the forced call is what stops the model
    writing prose instead.

    `tool_choice` was refused only against `config.yaml` until a review read
    the comment against the code — a caller's `model_settings` splatted after
    the harness's, so `tool_choice: "auto"` from a caller won and disabled the
    mechanism with nothing said. The asymmetry was invisible because the two
    settings sit four lines apart.
    """
    from dataclasses import dataclass

    from agents.testing import ScriptedModel

    from friday.agent.harness import Harness
    from friday.config import AgentConfig

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

    with pytest.raises(ValueError, match="tool_use_behavior"):
        build(tool_use_behavior="stop_on_first_tool")

    # An agent that declares neither is untouched: `max_tokens` and anything
    # else in `model_settings` still reaches the model.
    assert build(model_settings={"max_tokens": 64}).agent.model_settings.max_tokens == 64
