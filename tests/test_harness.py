"""Ticket 14 — one place an agent is run.

Everything every agent needs and none of them should restate: the client, the
settings, the logging hooks, the caps, and the rule that a failure becomes work
for a person rather than an exception nobody catches.

Written after the second agent existed, not before. One agent is a hypothetical
seam; building this against triage alone would have been guessing at what
varies.
"""

from __future__ import annotations

from agents import Agent
from agents.models.interface import Model
from agents.testing import ScriptedModel, assistant_message

from friday.config import AgentConfig
from friday.agent.harness import Harness, ToolContext, tool

CONFIG = AgentConfig(
    name="an-agent", api_key="sk-secret", base_url="https://example.invalid/v1",
    model="test-model", max_turns=1, settings={"temperature": 0}, options={},
)


def harness(*steps, **kw) -> Harness:
    return Harness(
        config=CONFIG,
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
    # the same trap `ask_for_fields` documents for its own `Literal`.

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
