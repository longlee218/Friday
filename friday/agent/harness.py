"""The one place an agent is run.

Owns everything every agent needs and none of them should restate: the client
and its `base_url` / `api_key` / `model`, the model settings, the logging hooks,
the turn cap, and the rule that a failure becomes work for a person rather than
an exception nobody catches.

An agent then declares only what makes it different — instructions, tools, what
it does with a tool call. Triage is one; the responder is the other.

Written *after* the second agent existed. One agent is a hypothetical seam, and
building this against triage alone would have meant guessing at what varies.
Guardrails and handoffs have somewhere to live here when they are wanted; they
are not invented ahead of a use.

**This is the only module that imports `agents`.** The SDK is here for speed,
not for keeps, and that is only true while replacing it means rewriting one
file. Everything another module needs from it — declaring a tool, the logging
hook base, an MCP server type — is re-exported below, under a name that does
not mention the library.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from typing import Any

from agents import (
    Agent,
    AgentHooks,
    ModelSettings,
    OpenAIChatCompletionsModel,
    RunConfig,
    RunContextWrapper,
    Runner,
    RunState,
    ToolsToFinalOutputResult,
    function_tool,
    set_tracing_disabled,
)
from agents.exceptions import ModelBehaviorError
from agents.mcp import (
    MCPServer,
    MCPServerSse,
    MCPServerStdio,
    create_static_tool_filter,
)
from agents.tool import default_tool_error_function
from agents.tool_context import ToolContext as _SdkToolContext
from openai import AsyncOpenAI

from friday.config import AgentConfig
from friday.ops.redact import scrub

__all__ = [
    "Harness",
    "Hooks",
    "MCPServer",
    "MCPServerSse",
    "MCPServerStdio",
    "ToolContext",
    "create_static_tool_filter",
    "stop_when",
    "tool",
]

log = logging.getLogger(__name__)

#: What a tool implementation needs from the SDK, under a name that does not
#: name it. `tool` decorates a function; `ToolContext` types its first
#: argument; `Hooks` is the base a logging or tracing hook subclasses.
#:
#: `ToolContext` was `RunContextWrapper` — the SDK's *parent* class, under the
#: SDK's name for the child. Harmless to the schema, because the check that
#: decides whether the first parameter is the context accepts either
#: (`function_schema.py`), but it hid what the runtime actually passes: a
#: `ToolContext` carrying `tool_name`, `tool_call_id`, `tool_arguments`,
#: `tool_call`, `tool_namespace`, `agent` and `run_config`. A tool that wants
#: to say which call it was could not, and nothing said why.
#:
#: **That check is on identity, not `issubclass`.** So this may be aliased to
#: `RunContextWrapper` or to `ToolContext` and to nothing else: point it at a
#: subclass of either — the natural move the day somebody wants one more field
#: — and the context parameter silently becomes one the model sees and has to
#: fill.
ToolContext = _SdkToolContext
Hooks = AgentHooks


def _tool_failed(ctx: RunContextWrapper, error: Exception) -> str:
    """What the model is told when a tool call fails.

    **A `ModelBehaviorError` is the model's to fix, so it keeps the SDK's own
    words.** The invoker catches every exception, and two of them are raised
    before the tool body runs at all: arguments that are not valid JSON, and
    arguments that fail the schema. The model can recover from both by
    emitting the call again correctly — so telling it the tool is unavailable
    would throw away the one recovery that works, and neither reason below
    applies anyway: nothing was written, and the message is the SDK's own
    string plus the model's own arguments.

    Everything else is the tool itself failing, and there the SDK's default
    formats `str(error)` into "An error occurred while running the tool.
    Please try again. Error: …" and hands that to the model. Two things are
    wrong with it, and neither is cosmetic:

    **It is a route out for text nobody chose to publish.** `_settle` keeps a
    provider exception out of a task's stored parameters for the same reason;
    this is the same class of exposure by a path that never reaches it. A
    store error carries the database path, an `OSError` from a skill file
    carries the filesystem — and `scrub` does not touch either, because it
    matches credential shapes and nothing else. What keeps them from the model
    is this function's return value, not the scrub.

    **"Please try again" is the wrong instruction for a tool that writes.**
    Retrying a write that may already have landed is how a row gets recorded
    twice. So the message says the opposite, for every tool, rather than
    leaving each one to remember.

    The exception is not swallowed: it is logged here, and the SDK logs it
    too, at ERROR with the tool's raw arguments and a traceback
    (`agents/tool.py`'s `_on_handled_error`). Both lines go through
    `friday/ops/redact.py`, so a credential in either is redacted — that
    filter had to learn to reach an exception argument and an `exc_info`
    before it was true, which is ticket 11's subject and was found by writing
    this sentence carelessly first. Only the model is told less.
    """
    if isinstance(error, ModelBehaviorError):
        return default_tool_error_function(ctx, error)
    log.warning("tool failed: %s", scrub(str(error)))
    return "that tool is unavailable right now — carry on without it"


def stop_when(recorded):
    """Stop the run when the agent has actually recorded something.

    For an agent whose answer arrives as a tool call. The obvious setting is
    `tool_use_behavior="stop_on_first_tool"`, and it is subtly wrong: it ends
    the run at the first tool call's **output**, and the SDK cannot tell a
    tool's success string from its failure string — a `failure_error_function`
    return value is the tool output. So a call the schema rejects, which the
    model could fix by emitting it again, instead becomes the run's final
    answer and nobody reads it.

    `recorded` is a predicate over the run's context — the capture object the
    tool writes into — and it says what "answered" actually means. Falsely, the
    model runs again and is handed the tool's output, which is the error
    message telling it what to correct.

    The retry budget is `max_turns` and nothing else: a bad call spends a turn,
    so an agent configured for one turn plus the one `run(extra_turns=1)` adds
    gets exactly one correction before the run is over and the failure becomes
    a person's. That is deliberate — a model that cannot get its own schema
    right twice is not going to on the third go, and this is the highest-volume
    path in the system.
    """

    def decide(ctx, results) -> ToolsToFinalOutputResult:
        if results and recorded(ctx.context):
            return ToolsToFinalOutputResult(
                is_final_output=True, final_output=results[-1].output
            )
        return ToolsToFinalOutputResult(is_final_output=False)

    return decide


def tool(func=None, **options):
    """`function_tool`, with this codebase's one default already applied.

    `failure_error_function` — see `_tool_failed`. Here rather than on each
    tool because seven call sites remembering a keyword is six chances to
    forget; a tool may still pass its own, since this only fills the gap.

    **`docstring_style` is deliberately not set**, and the reason is worth
    keeping because the opposite looks obviously right. The SDK picks between
    google, sphinx and numpy with a regex scorer of its own — griffe parses
    the docstring only once a style has been chosen, and its own auto-detection
    is an Insiders feature the SDK says it is approximating
    (`agents/function_schema.py`) — and pinning it seemed like insurance
    against a wrong guess dropping every `Args:` description silently.
    Measured, the insurance was the risk: detection returns `google` for every
    docstring here and falls back to `google` when it scores nothing, so the
    pin changed no schema — while a docstring written `:param x:` under a
    google pin loses its descriptions, which auto-detection reads correctly.
    The pin could only ever break the case it was there to protect.

    What actually guards this is `tests/test_tools.py`: every field of every
    tool must carry a description, whatever produced it.
    """
    options.setdefault("failure_error_function", _tool_failed)
    return function_tool(func, **options)


# Tracing is on by default and exports to OpenAI using the same key as model
# requests. With a third-party provider that leaks both the traffic and the
# credential, so it is switched off once, here, for every agent.
set_tracing_disabled(True)


class Harness:
    """Builds an agent from configuration, and runs it."""

    def __init__(
        self,
        *,
        config: AgentConfig,
        instructions: str,
        tools: list | None = None,
        #: Tool servers outside this process. Which ones an agent gets is
        #: composition, not something the agent declares.
        mcp_servers: list | None = None,
        #: What has been learned across earlier tasks — `friday/memory/notes.py`
        #: promotes them, and they arrive already rendered. In the
        #: instructions rather than the per-call input because they change
        #: only at a promotion, and a byte that moves early costs the cache
        #: hit on everything after it.
        #:
        #: Model-written, so it goes through the one seam that escapes rather
        #: than being concatenated on: a promoted note that closed its own
        #: section could put a `<critical_reminder>` into the instructions of
        #: every call that agent makes.
        notes: str = "",
        #: Where every call this agent makes is written down. An async callable
        #: taking one `ModelCall`.
        #:
        #: **Handed over once, here, rather than passed to `run()`** (D1). It
        #: was a `calls=` list on the call, and three of the four callers
        #: forgot it — the extractors, the summariser, and the responder
        #: through the pool — so `model_calls` held triage alone while the
        #: board described it as holding every prompt. A seam a caller can
        #: forget is one that will be forgotten; this one cannot be, because
        #: there is nowhere to forget it from.
        #:
        #: `None` records nothing, which is for tests and for an agent built
        #: before a sink exists. The composition root always passes one.
        record=None,
        model=None,
        context_type: type | None = None,
        **agent_options: Any,
    ) -> None:
        self._config = config
        self._record = record
        self.last_error: str | None = None
        agent_class = Agent[context_type] if context_type else Agent
        self.agent = agent_class(
            name=config.name,
            instructions=_with_notes(instructions, notes),
            model=model or _chat_model(config),
            tools=tools or [],
            mcp_servers=mcp_servers or [],
            model_settings=ModelSettings(**config.settings, **agent_options.pop(
                "model_settings", {}
            )),
            **agent_options,
        )

    @property
    def instructions(self) -> str:
        """What this agent was told it is, before any per-call context.

        Exposed so composition can be checked without reaching through this
        module into the SDK's own objects. `harness.py` is the only module
        that may know that shape, and a test that reads `.agent.instructions`
        would break on the swap this rule exists to keep cheap.
        """
        return self.agent.instructions or ""

    @property
    def tool_servers(self) -> list:
        """The tool servers this agent was handed. Same reason as above."""
        return list(self.agent.mcp_servers)

    async def run(
        self,
        prompt: str,
        *,
        context: Any = None,
        extra_turns: int = 0,
        message_id: str | None = None,
        task_id: int | None = None,
        node: str | None = None,
    ) -> Any | None:
        """Run it. `None` means it did not answer.

        Takes the user turn as a string; each family's own `prompt` module is
        what assembles it. This method assembles nothing. (There was a
        ContextBundle it also accepted — one dataclass with a slot for every
        family's sections, the last piece of shared shape after the families
        split, dissolved with ticket 45.)

        Every agent turns a non-answer into its own kind of work — a task for a human,
        or a fall back to a template — so none of them has to catch anything.
        The reason is kept in `last_error`, scrubbed, because it is stored
        against a task and a provider exception can quote an Authorization
        header.

        `extra_turns` is for an agent whose answer arrives as a tool call: the
        call and its result are two turns where a written answer is one.

        `message_id`, `task_id` and `node` are what this call was *about*, and
        they are three questions rather than one: a message suits triage, a
        task suits everything that works on one, and a node names the step of
        a graph that asked. A caller supplies whichever it knows. Forgetting
        one loses a correlation key; it does not lose the record — which is the
        whole difference between these and the `calls=` list they replaced.
        """
        return await self._settle(
            prompt,
            context=context,
            max_turns=self._config.max_turns + extra_turns,
            about=_About(message_id=message_id, task_id=task_id, node=node),
        )

    def checkpoint(self, result: Any) -> dict[str, Any]:
        """A run's pending tool approvals, serialized. `result.interruptions`
        empty means there is nothing to resume — a node checks that itself
        before calling this; it is not this method's job to.
        """
        return result.to_state().to_json()

    async def resume(
        self,
        interruption: dict[str, Any],
        *,
        context: Any = None,
    ) -> Any | None:
        """Continue a run that was approved to go ahead — `checkpoint`'s
        output, from this process or an earlier one.

        `context` is a fresh instance of whatever type the paused call used,
        not the one that call closed over — that object is gone once the
        process that made it is. A tool called during the resumed turns
        writes into this one the same way it wrote into the original; a
        caller that wants to read what a tool did on resume reads it back
        from here, same as it would from a fresh `run()`. The SDK logs a
        warning about the serialized context not restoring on its own — it
        does not, which is exactly why this parameter exists; passing one
        is what fixes it, not a sign that something failed.

        Approval only: a decline needs no further turn with the model — the
        caller already knows it declined, and building whatever that becomes
        (ticket 07: a `HandOver`) is its business, not a reason to ask the
        model to react to its own refusal.

        There is no `extra_turns` here: the SDK bakes `max_turns` into the
        state at the call that paused, and a different value passed on
        resume is silently ignored. The run that calls a `needs_approval`
        tool needs enough headroom for both halves — reaching the call, and
        whatever happens once it is approved — from its own `run()` call.
        """
        state = await RunState.from_json(
            self.agent, interruption, context_override=context
        )
        pending = state.get_interruptions()
        if len(pending) != 1:
            # A model may emit two calls in one turn — ordinary parallel tool
            # calling — and then one approval does not say which. Guessing
            # would apply something nobody said yes to. This used to unpack
            # into a single name, which raised past every caller from outside
            # the try below, so the module's own rule — a failure is `None`
            # and a `last_error`, never an exception — did not reach it.
            self.last_error = (
                f"expected one call awaiting approval, found {len(pending)}"
            )
            log.warning("%s cannot resume: %s", self._config.name, self.last_error)
            return None
        state.approve(pending[0])
        return await self._settle(
            state, context=None, max_turns=self._config.max_turns
        )

    async def _settle(
        self,
        input_: Any,
        *,
        context: Any,
        max_turns: int,
        about: "_About | None" = None,
    ) -> Any | None:
        """Run to completion or to the first thing that stops it, and turn a
        failure into `last_error` rather than an exception every caller would
        otherwise have to catch identically.

        The one place a model call happens, so the one place it can be bounded
        and written down (D2). `AgentHooks` cannot do either: `on_llm_start`
        fires after the decision to spend has been made and `on_llm_end` after
        the money is gone, and neither can refuse a call or cut one short.
        """
        # Deferred: `llm_log` reaches `Hooks` through this module, so importing
        # it at module load time would be a cycle.
        from friday.agent.llm_log import LogHooks

        self.last_error = None
        calls: list = []
        hooks = LogHooks(calls, model=self._config.model)
        self.agent.hooks = hooks
        try:
            return await asyncio.wait_for(
                Runner.run(
                    self.agent,
                    input_,
                    context=context,
                    max_turns=max_turns,
                    run_config=RunConfig(tracing_disabled=True),
                ),
                timeout=self._config.timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001 - every failure becomes work
            self.last_error = _why(exc, self._config.timeout_seconds)
            log.warning("%s failed: %s", self._config.name, self.last_error)
            return None
        finally:
            # In `finally` because a call that failed still cost what it cost.
            # A timeout cancels the run *inside* the provider request, so the
            # hook that builds a `ModelCall` never fires — and for every agent
            # configured `max_turns: 1`, which is triage, the extractors and
            # the summariser, that would mean no record at all of the call that
            # hung. `unfinished()` is what the run managed to send.
            if (cut_off := hooks.unfinished()) is not None:
                calls.append(cut_off)
            await self._write_down(calls, about)

    async def _write_down(self, calls: list, about: "_About | None") -> None:
        """Hand each call to the sink. A sink that fails costs a row, not a run.

        A failed write is a lost row; a raised write would be a lost answer,
        and the agent has already done the expensive part by the time this
        runs.

        **Cancellation is the exception, and deliberately so.** This awaits
        inside a `finally`, so cancelling the run while a write is in flight
        delivers `CancelledError` at that await — and `except Exception` does
        not catch it, so the loop stops and the remaining calls are dropped.
        Widening the catch would swallow the cancellation and keep a shutting-
        down process writing rows, which is worse than losing them: the caller
        has already stopped waiting for the answer these rows describe. So the
        rule is that the record survives every failure except the one that
        means "stop", and `test_a_cancelled_run_stops_rather_than_finishing_its_writes`
        is what says so out loud.
        """
        if self._record is None:
            return
        for call in calls:
            try:
                await self._record(about.stamp(call) if about else call)
            except Exception:  # noqa: BLE001 - recording must not cost the run
                log.exception("could not record a call by %s", self._config.name)


@dataclass(frozen=True, slots=True)
class _About:
    """What a run was about, for the rows it produces.

    A value rather than three parameters threaded through `_settle`, because
    they travel together and are read together, and because the next one —
    ticket 04's `attempts` — is measured here rather than passed in.
    """

    message_id: str | None = None
    task_id: int | None = None
    node: str | None = None

    def stamp(self, call):
        """The call with what the caller knew about it, and nothing else
        overwritten: `latency_ms` was measured by the hook and is not ours."""
        if not (self.message_id or self.task_id or self.node):
            return call
        return replace(
            call,
            message_id=self.message_id,
            task_id=self.task_id,
            node=self.node,
        )


def _why(exc: Exception, timeout: float) -> str:
    """The reason, in a form somebody can act on.

    A timeout is the case this exists for: `asyncio.wait_for` raises a
    `TimeoutError` whose `str()` is the empty string, so the module's own rule
    — a failure is a `last_error` a person reads — would have produced
    "triage failed: " and nothing else.

    Everything else is scrubbed, because it is stored against a task and a
    provider exception can quote an Authorization header.
    """
    if isinstance(exc, asyncio.TimeoutError):
        return f"no answer within {timeout:g}s"
    return scrub(str(exc))


def _with_notes(instructions: str, notes: str) -> str:
    """Instructions, then what has been learned, as a section like everything
    else in a prompt.

    Deferred import: `instruction_prompt` is where every other value is
    escaped, and importing it at module load would be a cycle — it reaches
    `Hooks` and the memory types through paths that come back here.
    """
    if not notes or not notes.strip():
        return instructions
    from friday.agent.instruction_prompt import memory

    return f"{instructions}\n{memory(notes_body=notes).render()}"


def _chat_model(config: AgentConfig) -> OpenAIChatCompletionsModel:
    """Chat Completions rather than the Responses API, so `base_url`, `api_key`
    and `model` are the whole of what it takes to use a different
    OpenAI-compatible provider."""
    client = AsyncOpenAI(base_url=config.base_url, api_key=config.api_key)
    return OpenAIChatCompletionsModel(model=config.model, openai_client=client)
