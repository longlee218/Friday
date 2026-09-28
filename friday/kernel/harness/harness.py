"""The one place an agent is run.

Owns everything every agent needs and none of them should restate: the client
and its `base_url` / `api_key` / `model`, the model settings, the logging hooks,
the turn cap, and the rule that a failure becomes work for a person rather than
an exception nobody catches.

An agent then declares only what makes it different — instructions, tools, what
it does with a tool call. Triage is one; the responder is the other.

**This module and `friday/sdk/testing/` are the only two that import the agent
SDK.** Pydantic AI is here behind a Friday-owned API (`Harness`, `run`,
`run_structured`, `tool`, `ToolContext`), so moving to another framework — or
another vendor Pydantic AI already speaks — is a rewrite of this one file.
Everything another module needs from it — declaring a tool, the run context
type, an MCP toolset, the hook base — is re-exported below, under a name that
does not mention the library, so `mcp.py` and `llm_log.py` take the vendor's
names *through here* rather than importing it. `friday/sdk/testing/` is the one
exception to the import rule: the test-double seam, whose whole job is to be the
single place a *test* names the vendor.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, fields as dataclass_fields, replace
from typing import Any, cast, get_type_hints

from pydantic import TypeAdapter
from pydantic_ai import (
    Agent,
    ModelRetry,
    RunContext,
    Tool,
    ToolOutput,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
    UsageLimits,
    capture_run_messages,
)
from pydantic_ai.capabilities import Hooks
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings
from fastmcp.client.transports import (
    SSETransport,
    StdioTransport,
    StreamableHttpTransport,
)
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    InternalServerError,
    RateLimitError,
)

from friday.kernel.harness.structured import Unfit, describe, find_json, fits
from friday.kernel.config import AgentConfig
from friday.kernel.domain.models import FridayState
from friday.sdk.tools import ToolSpec

__all__ = [
    "Harness",
    "Hooks",
    "MCPToolset",
    "ModelRetry",
    "SSETransport",
    "StdioTransport",
    "StreamableHttpTransport",
    "ToolContext",
    "tool",
]

log = logging.getLogger(__name__)

#: The run context a tool reads its per-run state off — an alias of
#: `RunContext`, so a tool declares `ctx: ToolContext[FridayState]` without
#: naming the library. Pydantic AI hides a `RunContext`-typed first parameter
#: from the tool's JSON schema by design, so the model never sees it; the state
#: itself is `ctx.deps`.
ToolContext = RunContext

#: Attempts — a repeat of the same failed thing, never progress (board
#: `domains-plug-in`, ticket 17). The same for every agent, so core constants.
#:
#: How many times to call the provider for one run before giving up, and how
#: long to wait between two attempts — the same wait every time, no doubling
#: (the operator's call, 2026-09-28). A request is bounded by its agent's
#: `request_timeout_seconds` (each attempt at that × its requests), and a
#: timed-out attempt is retried like a 429, so a one-request 60s agent's worst
#: run is `PROVIDER_ATTEMPTS` × 60s plus nine waits — 690s. The pool works only `workflows.concurrency` tasks at
#: once, so that is how long a stalled provider can hold a slot.
PROVIDER_ATTEMPTS = 10
PROVIDER_BACKOFF_SECONDS = 10.0
#: How many goes an answer that does not fit its shape gets at fixing itself.
#: A model that cannot get its own schema right twice will not on the third
#: go, and every attempt is billed.
OUTPUT_CORRECTIONS = 1


def tool(func=None, **options):
    """A Pydantic AI `Tool`, built from a plain function.

    A thin wrapper so tool modules name `tool` rather than the vendor — a tool
    declaration is what pulls the SDK in, so routing it through here is what
    keeps `harness.py` the only importer. Pydantic AI infers from the signature
    whether the first parameter is the run context (`ctx: ToolContext[...]`) and
    hides it from the model's schema; a google-style `Args:` docstring becomes
    each parameter's description, which `tests/test_tools.py` requires every
    field to carry.

    **A tool that fails is handled elsewhere, deliberately.** The rule "a failed
    tool becomes 'carry on without it' rather than an error the model retries"
    lives in the run's hooks (`friday/kernel/harness/llm_log.py`'s `tool_execute_error`),
    where it can also record the failure — the substitution and the record are
    one decision and belong together, and doing it per run rather than per tool
    is why this wrapper adds no `failure_error_function` of its own. A tool that
    wants the model to *fix* its call raises `ModelRetry` itself; anything else
    it raises is turned into the "unavailable" message there.

    A plugin, which may not import this module, declares its tools as neutral
    `ToolSpec`s through `friday.sdk.tools.tool` instead; `_bind_tool_spec` binds
    those to a `Tool` here, the one place that names the SDK.
    """

    def make(fn):
        return Tool(fn, **options)

    return make(func) if func is not None else make


def _bind_tool_spec(spec: Any) -> Any:
    """A vendor `Tool` from whatever a caller handed in `tools=`.

    A plugin declares a tool as a neutral `ToolSpec` (`friday.sdk.tools`) so it
    never names the SDK; this is the one place that turns each into the vendor's
    `Tool(fn, **options)`, the same call `tool` above makes — Pydantic AI then
    infers the run-context parameter and the docstring arg descriptions exactly
    as before. A `Tool` already built (or a plain callable) is passed through,
    so a caller that has one is not made to unwrap it.
    """
    if isinstance(spec, ToolSpec):
        return Tool(spec.fn, **spec.options)
    return spec


def _ends_fn(spec: Any) -> Any:
    """The function behind a terminal output tool — a `ToolSpec`'s, or a plain
    callable passed through."""
    return spec.fn if isinstance(spec, ToolSpec) else spec


def _ends_type(spec: Any) -> type | None:
    """The type a terminal output tool returns, read off its return annotation,
    so `run_structured` can recognise its output. `None` when it is unannotated
    or the annotation will not resolve to a class — then the run's output is
    matched only against `answers`."""
    try:
        got = get_type_hints(_ends_fn(spec)).get("return")
    except Exception:  # noqa: BLE001 - an unresolved annotation is not fatal here
        return None
    return got if isinstance(got, type) else None


class Harness:
    """Builds an agent from configuration, and runs it."""

    #: Class-level default so `run_structured` is safe on a test double that
    #: subclasses this and skips `__init__`; a built harness overrides it in
    #: `__init__` with the terminal tools' result types.
    _ends_with_types: tuple[type, ...] = ()

    def __init__(
        self,
        *,
        config: AgentConfig,
        instructions: str,
        tools: list | None = None,
        #: Tool servers outside this process, as Pydantic AI toolsets. Which
        #: ones an agent gets is composition, not something the agent declares.
        mcp_servers: list | None = None,
        #: Where every call this agent makes is written down. An async callable
        #: taking one `ModelCall`. Handed over once, here, rather than passed to
        #: `run()` (D1): a seam a caller can forget is one that will be
        #: forgotten. `None` records nothing, which is for tests.
        record=None,
        model=None,
        #: The skill library this agent may reach, or `None`. Given it, the
        #: harness wires the four skill tools itself — a thing every agent needs
        #: is not four lines every agent has to remember. An empty library is
        #: the same as none.
        skills=None,
        #: The shape this agent's answer has, when it has one — a dataclass.
        #: Declared here rather than per call because an agent's answer shape
        #: does not vary per call, so its output tool and correction budget are
        #: built exactly once.
        answers: type | None = None,
        #: Extra ways this agent may **finish** besides its `answers` shape —
        #: terminal output tools, as `ToolSpec`s (or plain functions). Calling
        #: one ends the run with that tool's return value as the output, the
        #: same way the answer tool ends it with the answer. The diagnose loop
        #: offers `hand_over(reason)` this way, so the model can end the
        #: investigation by escalating to the operator rather than by answering.
        #: `run_structured` hands the return value back as-is; the caller
        #: branches on its type. Requires `answers` (a finish beside an answer).
        ends_with: list | None = None,
        context_type: type | None = None,
        **agent_options: Any,
    ) -> None:
        self._config = config
        self._record = record
        self._instructions = instructions
        self.last_error: str | None = None
        #: Set when the model answered and the answer did not fit `answers`:
        #: why, and which of the shape's fields said so. Read immediately after
        #: `run_structured` returned `None`, and nowhere else (D20).
        #:
        #: Triage is the caller that needs it: "the model named a task type that
        #: does not exist" and "the model wrote nonsense in the confidence" are
        #: one validation failure and two different things to tell an operator,
        #: and `Unfit.fields` is what answers that without matching a substring
        #: against a sentence written for a model.
        self.unfit: Unfit | None = None
        #: One run of this harness at a time, taken by `_settle` for the whole
        #: of a run — the budget check, the provider, the record. The pool works
        #: tasks side by side (ticket 13), and one extractor per type and one
        #: responder serve every task, so two runs of one harness at once is the
        #: ordinary case. **Serialised rather than made local** because the three
        #: flags above are read by the caller *after* the run returns, and that
        #: is safe only because nothing awaits between `_settle` releasing this
        #: lock and the caller reading them.
        self._one_run = asyncio.Lock()
        #: The catalogue, for whoever builds this agent's instructions. Read off
        #: the same object the tools came from, so the prompt and the tools
        #: cannot describe different skills.
        self.skills: list[str] = (
            list(skills.catalogue()) if skills is not None and len(skills) else []
        )
        tool_list = list(tools or [])
        if self.skills:
            from friday.kernel.tools.describe_skill import describe_skill_tool
            from friday.kernel.tools.fetch_skill import fetch_skill_tool
            from friday.kernel.tools.read_skill_file import read_skill_file_tool
            from friday.kernel.tools.search_skills import search_skills_tool

            tool_list += [
                fetch_skill_tool(skills),
                search_skills_tool(skills),
                describe_skill_tool(skills),
                read_skill_file_tool(skills),
            ]
        # A tool arrives as a neutral `ToolSpec` (a plugin declaring one names no
        # vendor) or as an already-built `Tool`; bound to the SDK's `Tool` here,
        # the one place that names it.
        tool_list = [_bind_tool_spec(t) for t in tool_list]
        self._tools = tool_list
        self._toolsets = list(mcp_servers or [])

        self.answers = answers
        #: The result types of the terminal output tools in `ends_with`, so
        #: `run_structured` recognises one of their returns as a finished run
        #: rather than a shape that did not fit `answers`.
        self._ends_with_types: tuple[type, ...] = ()
        output_type: Any = str
        retries: Any = None
        if answers is None and ends_with:
            raise ValueError(
                "ends_with is a finish beside an answer; it needs answers="
            )
        if answers is not None:
            # **Two things a caller may not override**, because they are the
            # mechanism, not a default: the output tool is what forces a
            # structured answer (Pydantic AI forces the output tool when no text
            # output is allowed), and the `OUTPUT_CORRECTIONS` retry is the one
            # correction turn. A caller passing `output_type`, `retries`, or a
            # `tool_choice` in `model_settings` is asking for something that
            # cannot work — a `tool_choice` other than the forced output tool
            # disables the forcing — so it is refused rather than quietly
            # honoured.
            owned: list[str] = [
                name for name in ("output_type", "retries") if name in agent_options
            ]
            if "tool_choice" in agent_options.get("model_settings", {}):
                owned.append("tool_choice")
            if owned:
                raise ValueError(
                    f"an agent that declares `answers=` owns "
                    f"{' and '.join(owned)}; passing one would silently disable "
                    f"the mechanism it was built around"
                )
            answer_output = ToolOutput(
                self._answer_output(answers),
                name=ANSWER,
                description=(
                    f"Give your answer. Call this exactly once, with these "
                    f"fields:\n{describe(answers)}"
                ),
            )
            retries = {"output": OUTPUT_CORRECTIONS}
            if ends_with:
                # Each terminal tool is a second output tool: Pydantic AI ends
                # the run when the model calls any output tool, so calling
                # `hand_over` finishes the run with a `HandOver` the same way
                # the answer tool finishes it with a `Diagnosis`. **Named
                # explicitly** — an unnamed `ToolOutput` in a union registers as
                # `final_result_<fn>`, not the function's own name, which no
                # caller can predict; the schema and description still come from
                # the function's signature and docstring, the way a tool's do.
                terminals = []
                types: list[type] = []
                # The answer tool's name and each terminal's must be distinct —
                # duplicate output-tool names are undefined in Pydantic AI.
                names = {ANSWER}
                for spec in ends_with:
                    fn = _ends_fn(spec)
                    result_type = _ends_type(spec)
                    if result_type is None:
                        # **Registered but unrecognisable is worse than
                        # refused.** Without a resolvable return type the tool is
                        # still a callable output — the model can end the run
                        # with it — but `run_structured` cannot match its output,
                        # so the outcome is silently lost as "no answer". Refuse
                        # at build time instead, where it names the tool.
                        raise ValueError(
                            f"terminal tool {getattr(fn, '__name__', fn)!r} needs "
                            f"a return annotation that resolves to a class, so "
                            f"run_structured can hand its output back"
                        )
                    opts = spec.options if isinstance(spec, ToolSpec) else {}
                    name = opts.get("name") or fn.__name__
                    if name in names:
                        raise ValueError(
                            f"terminal tool name {name!r} collides with the "
                            f"answer tool or another terminal tool"
                        )
                    names.add(name)
                    types.append(result_type)
                    terminals.append(
                        ToolOutput(
                            fn,
                            name=name,
                            description=(
                                opts.get("description")
                                or (fn.__doc__ or "").strip()
                                or None
                            ),
                        )
                    )
                self._ends_with_types = tuple(types)
                output_type = [answer_output, *terminals]
            else:
                output_type = answer_output

        # `config.settings` first, then a caller's `model_settings`, so what a
        # caller wired wins over the file — the direction `answers=` needs, and
        # the merge (rather than two splats) avoids a `TypeError` at
        # construction when a key appears in both.
        settings = cast(
            ModelSettings,
            {**config.settings, **agent_options.pop("model_settings", {})},
        )
        if answers is not None:
            # Forcing the output tool is native — Pydantic AI forces it when no
            # text output is allowed — so a `tool_choice` carried in from the
            # file would fight it (`'required'` even *excludes* output tools).
            # Dropped rather than honoured, for the same reason the caller's is
            # refused above: it can only disable the mechanism.
            settings.pop("tool_choice", None)

        self.agent = Agent(
            model or _chat_model(config),
            output_type=output_type,
            instructions=instructions,
            deps_type=context_type if context_type is not None else FridayState,
            name=config.name,
            model_settings=settings,
            retries=retries,
            tools=tool_list,
            toolsets=self._toolsets,
            **agent_options,
        )

    def _answer_output(self, schema: type):
        """The function the answer tool runs, generated from the shape.

        Its signature is `**data`, so Pydantic AI passes the model's arguments
        through untouched and validation is `fits`'s — which drops unknown keys,
        tells "all keys unknown" from "empty", and produces an `Unfit` that
        names the field but quotes none of the arguments (an extractor's
        arguments are reporter-controlled text). On a bad call it raises
        `ModelRetry(problem.why)`, which reaches the model as the tool's own
        correction; `OUTPUT_CORRECTIONS` decides how many goes it gets.

        **`_refused` is how the harness hears about a turned-down call**, and it
        exists because the run may never get back to tell it: a model that
        answers wrongly twice exhausts the retry and the run raises, so the only
        thing that knows the answer was *refused* rather than *absent* is this
        body. See `_refused`.
        """

        def answer(**data: Any) -> Any:
            value, problem = fits(data, schema)
            if problem is None:
                return value
            self._refused(problem)
            raise ModelRetry(f"that did not fit: {problem.why}")

        return answer

    def _refused(self, problem: Unfit) -> None:
        """The answer tool turned a call down, and why. On the instance beside
        `last_error`, and safe there for the same reason: one run
        of a harness at a time, and the caller reads the flag before anything
        awaits."""
        self.unfit = problem

    @property
    def instructions(self) -> str:
        """What this agent was told it is, before any per-call context. Exposed
        so composition can be checked without reaching through this module into
        the SDK's own objects."""
        return self._instructions or ""

    @property
    def tool_servers(self) -> list:
        """The tool servers (toolsets) this agent was handed."""
        return list(self._toolsets)

    @property
    def tools(self) -> list:
        """Every tool this agent can reach, including the ones this class wired
        itself — the four skill tools. Not the answer tool an `answers=` agent
        finishes through: that is the run's output, not a door the agent chooses
        among."""
        return list(self._tools)

    async def run(
        self,
        prompt: str,
        *,
        context: Any = None,
        message_id: str | None = None,
        task_id: int | None = None,
        node: str | None = None,
    ) -> Any | None:
        """Run it. `None` means it did not answer.

        Takes the user turn as a string; each family's own `prompt` module is
        what assembles it. Returns the run's result, whose text answer is
        `.output`; a non-answer is turned into `None` and a reason in
        `last_error`, scrubbed, so no caller has to catch anything.

        The run stops at the declaration's `max_turns` — every request, tool
        turns included — or its `tokens`, whichever comes first.

        `message_id`, `task_id` and `node` are what this call was *about*. A
        caller supplies whichever it knows; a `FridayState` as `context` already
        knows the message and the task (D8), so a caller passing one need not
        name them.
        """
        return await self._settle(
            prompt,
            context=context,
            max_turns=self._config.max_turns,
            about=_About.of(
                context, message_id=message_id, task_id=task_id, node=node
            ),
        )

    async def run_structured(
        self,
        prompt: str,
        *,
        context: Any = None,
        message_id: str | None = None,
        task_id: int | None = None,
        node: str | None = None,
    ) -> Any | None:
        """Ask for this agent's declared shape, and hand back only an answer
        that actually fits it.

        Returns an instance of `answers`, or `None` — and `None` means nobody
        got an answer, never "an answer with nothing in it".

        **The answer arrives as a tool call, forced** (D3): Pydantic AI forces
        the output tool because no text output is allowed, and the tool's own
        body validates the arguments with `fits` in this process — nothing is
        sent on the wire to enforce the shape, because the configured provider
        accepts a `json_schema` response format and then ignores it.

        **One correction, and it is the retry budget** (D4): a call that does
        not fit comes back to the model as the tool's own output, naming the
        field, and `OUTPUT_CORRECTIONS` is the single go at fixing it. No
        second run.

        **A written answer is still read** (D13): forcing is not a guarantee —
        the same probe that measured clean tool arguments also measured the
        model answering in prose. When the run produces no fitting tool answer,
        the reply captured from the model is searched for the object and checked
        against the same shape. The cost is that a prose answer takes the
        correction turn the forced tool would otherwise have used, rather than
        being read on the first turn.
        """
        if self.answers is None:
            raise ValueError(
                f"{self._config.name} was not built with `answers=`, so there "
                f"is no shape to ask for"
            )
        with capture_run_messages() as messages:
            said = await self.run(
                prompt,
                context=context,
                message_id=message_id,
                task_id=task_id,
                node=node,
            )
        if said is not None and isinstance(said.output, self.answers):
            # A run that answered — corrected or not — is not an unfit run.
            # `_refused` fires per turned-down call; clearing it here makes the
            # flag mean "this run produced no answer that fits".
            self.unfit = None
            return said.output

        if (
            said is not None
            and self._ends_with_types
            and isinstance(said.output, self._ends_with_types)
        ):
            # A terminal output tool finished the run (e.g. `hand_over`): its
            # return is the run's outcome, not an answer that failed to fit
            # `answers`. Hand it back as-is for the caller to branch on.
            self.unfit = None
            return said.output

        # The written-answer fallback (D13). Forcing the tool is not a
        # guarantee, so a reply the model wrote as prose is searched for the
        # object and checked against the same shape. In a real run that prose
        # only survives on the captured messages (a text answer fails the forced
        # tool); a `run()` that hands back a string output directly carries it
        # there, which is the seam a `ScriptedHarness` uses.
        written = (
            said.output
            if said is not None and isinstance(said.output, str)
            else _last_text(messages)
        )
        data = find_json(written)
        if data is not None:
            value, problem = fits(data, self.answers)
            if problem is None:
                self.unfit = None
                self.last_error = None
                return value
            self._refused(problem)
            self.last_error = (
                f"the answer did not fit {self.answers.__name__}: {problem.why}"
            )
            log.warning("%s: %s", self._config.name, self.last_error)
            return None

        # No fitting tool answer and no written object. The run itself set the
        # reason it did not answer — a provider failure, a spent budget,
        # or the forced tool exhausting its correction — and that reason is the
        # true one; only replace it when the run left none.
        if self.last_error is None:
            self.last_error = f"there was no {self.answers.__name__} in the reply"
            log.warning("%s: %s", self._config.name, self.last_error)
        return None

    async def _settle(
        self,
        prompt: Any,
        *,
        context: Any,
        max_turns: int,
        about: "_About | None" = None,
    ) -> Any | None:
        """Run to completion or to the first thing that stops it, and turn a
        failure into `last_error` rather than an exception every caller would
        otherwise have to catch identically.

        The one place a model call happens, so the one place it can be bounded
        and written down (D2). One at a time per harness — see `_one_run`.
        """
        async with self._one_run:
            return await self._settle_alone(
                prompt, context=context, max_turns=max_turns, about=about
            )

    async def _settle_alone(
        self,
        prompt: Any,
        *,
        context: Any,
        max_turns: int,
        about: "_About | None" = None,
    ) -> Any | None:
        # Deferred: `LogHooks` reaches names through this module, so importing it
        # at module load time would be a cycle.
        from friday.kernel.harness.llm_log import LogHooks

        self.last_error = None
        # Cleared here rather than in `run_structured`: `_settle` is the one
        # place every run begins, so a harness with an `answers=` shape called
        # through plain `run()` cannot read a flag left by the run before it.
        self.unfit = None

        calls: list = []
        reached: list = []
        hooks = LogHooks(
            calls, model=self._config.model, tools=reached, agent=self._config.name
        )
        progress = _Progress()
        try:
            # No clock on the run (board `domains-plug-in`, ticket 17): it stops
            # on turns or tokens, and a tool call carries its own timeout.
            return await self._attempts(
                prompt, context, max_turns, hooks, calls, progress
            )
        except Exception as exc:  # noqa: BLE001 - every failure becomes work
            self.last_error = _why(exc, progress.attempt)
            log.warning("%s failed: %s", self._config.name, self.last_error)
            return None
        finally:
            # In `finally` because a call that failed still cost what it cost. A
            # cancelled run stops inside the provider request, so the hook
            # that builds a `ModelCall` never fires — `unfinished()` is what the
            # run managed to send.
            progress.flush(hooks, calls)
            await self._write_down(calls + reached, about)

    async def _attempts(self, prompt, context, max_turns, hooks, calls, progress):
        """Call the provider until it answers, it refuses in a way trying again
        cannot fix, or the attempts run out.

        Ours rather than the client's, and the client's is switched off in
        `_chat_model`: `AsyncOpenAI` retries by default and says nothing, so the
        provider bills three calls where the record holds one.
        """
        # Room for the output correction on top of the turn budget: a
        # correction is an attempt, not a turn, so the request limit is never
        # what stops a structured answer from being fixed.
        request_limit = max_turns + (
            OUTPUT_CORRECTIONS if self.answers is not None else 0
        )
        # Counted per `agent.run`, so each provider attempt starts its own
        # count: a run with hiccups can spend up to `PROVIDER_ATTEMPTS` times
        # `tokens`. Accepted while the caps are placeholders (ticket 01).
        limits = UsageLimits(
            request_limit=request_limit, total_tokens_limit=self._config.tokens
        )
        # `settings["timeout"]` reaches the HTTP client as a limit on silence
        # between bytes, not on a whole request, so the attempt itself is
        # bounded too: each of its requests may take the full timeout.
        timeout = self._config.settings.get("timeout")
        attempt_timeout = None if timeout is None else timeout * request_limit
        last: Exception | None = None
        for attempt in range(1, PROVIDER_ATTEMPTS + 1):
            progress.attempt = attempt
            try:
                result = await asyncio.wait_for(
                    self.agent.run(
                        prompt,
                        deps=context,
                        usage_limits=limits,
                        capabilities=[hooks.capability],
                    ),
                    timeout=attempt_timeout,
                )
            except Exception as exc:  # noqa: BLE001 - decided by _transient
                progress.flush(hooks, calls)
                last = exc
                if not _transient(exc) or attempt == PROVIDER_ATTEMPTS:
                    raise
                log.info(
                    "%s: attempt %d of %d failed (%s), trying again",
                    self._config.name,
                    attempt,
                    PROVIDER_ATTEMPTS,
                    type(exc).__name__,
                )
                await asyncio.sleep(PROVIDER_BACKOFF_SECONDS)
            else:
                progress.flush(hooks, calls)
                return result
        raise last  # unreachable: the loop either returns or raises

    async def _write_down(self, calls: list, about: "_About | None") -> None:
        """Hand each call to the sink. A sink that fails costs a row, not a run.

        **Cancellation is the exception, and deliberately so.** This awaits
        inside a `finally`, so cancelling the run while a write is in flight
        delivers `CancelledError` at that await — and `except Exception` does not
        catch it, so the loop stops and the remaining calls are dropped. The
        record survives every failure except the one that means "stop".
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
    """What a run was about, for the rows it produces. A value rather than three
    parameters threaded through `_settle`, because they travel together."""

    message_id: str | None = None
    task_id: int | None = None
    node: str | None = None

    @classmethod
    def of(
        cls,
        context: Any,
        *,
        message_id: str | None = None,
        task_id: int | None = None,
        node: str | None = None,
    ) -> "_About":
        """What this call was about, read off the run's state where there is one
        (D8) and named explicitly where there is not. An explicit argument still
        wins, for a caller that knows better than the state it was handed.
        `node` is never on the state — it is which step of a graph asked."""
        if isinstance(context, FridayState):
            message_id = message_id or context.message_id
            task_id = task_id if task_id is not None else context.task_id
        return cls(message_id=message_id, task_id=task_id, node=node)

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


#: What is worth calling again. A list rather than a guess from the message: a
#: 400 is the provider saying the request itself is wrong, and paying to ask it
#: a second time buys nothing.
_TRANSIENT = (
    APIConnectionError, APITimeoutError, RateLimitError, InternalServerError,
    # An attempt past its bound (`_attempts`).
    TimeoutError,
)

#: Statuses the SDK gives no class of its own, and that are still worth another
#: call. Only 408 today.
_RETRY_STATUSES = frozenset({408})


def _transient(exc: Exception) -> bool:
    if isinstance(exc, (UnexpectedModelBehavior, UsageLimitExceeded)):
        # The model exhausted its correction budget, or the run hit its turn
        # cap. Trying again changes neither — they are the run's own verdict,
        # not the provider's.
        return False
    if isinstance(exc, _TRANSIENT):
        return True
    return isinstance(exc, APIStatusError) and exc.status_code in _RETRY_STATUSES


@dataclass
class _Progress:
    """Which attempt is in flight, and how much of the record it has claimed."""

    attempt: int = 1
    #: How many rows already carry a number. Everything after this belongs to
    #: the attempt in flight.
    claimed: int = 0

    def flush(self, hooks, calls: list) -> None:
        """Number every row this attempt produced, and keep what it sent."""
        if (cut_off := hooks.unfinished()) is not None:
            calls.append(cut_off)
        for index in range(self.claimed, len(calls)):
            calls[index] = replace(calls[index], attempt=self.attempt)
        self.claimed = len(calls)


def _why(exc: Exception, attempts: int = 0) -> str:
    """The reason, in a form somebody can act on — scrubbed, because it is
    stored against a task and a provider exception can quote an Authorization
    header."""
    from friday.kernel.ops.redact import scrub

    # `asyncio.wait_for` raises a `TimeoutError` whose `str()` is empty.
    said = "no answer within the request timeout" if isinstance(
        exc, TimeoutError
    ) else scrub(str(exc))
    if attempts > 1:
        return f"gave up after {attempts} attempts: {said}"
    return said


def _last_text(messages: list) -> str:
    """The last text a model wrote across the captured run, for the D13
    written-answer fallback. Empty when every turn was a tool call — which is
    the ordinary, forced path, and the case that has no written answer to
    read."""
    text = ""
    for message in messages:
        if isinstance(message, ModelResponse):
            for part in message.parts:
                if isinstance(part, TextPart):
                    text = part.content
    return text


def _chat_model(config: AgentConfig) -> OpenAIChatModel:
    """Chat Completions rather than the Responses API, so `base_url`, `api_key`
    and `model` are the whole of what it takes to use a different
    OpenAI-compatible provider.

    The client is built here rather than left to the provider's default so its
    retries can be switched off (retrying is `_attempts`'s job, where it can be
    seen and counted). Its timeout is the client's own default — the one
    unbounded wait left, and the operator's call (board `domains-plug-in`,
    ticket 17).
    """
    client = AsyncOpenAI(
        base_url=config.base_url,
        api_key=config.api_key,
        max_retries=0,
    )
    return OpenAIChatModel(config.model, provider=OpenAIProvider(openai_client=client))


#: What the model calls to answer. One name for every shape, because an agent
#: built with `answers=` has exactly one way to finish.
ANSWER = "answer"


def _answer_params(schema: type) -> dict[str, Any]:
    """The answer shape as a JSON schema, with each field's own `doc` on it.

    Kept as the canonical description of the shape for the model — the same
    `doc` metadata `describe` renders into the prompt — and read by
    `tests/test_tools.py`, which requires every field of every tool to carry a
    description. The class's own docstring is developer prose and is dropped:
    what the model needs about the shape as a whole is on the tool's
    description, generated from the fields.
    """
    described = TypeAdapter(schema).json_schema()
    described.pop("description", None)
    properties = described.get("properties", {})
    for field in dataclass_fields(schema):
        doc = field.metadata.get("doc")
        if doc and field.name in properties:
            properties[field.name].setdefault("description", doc)
    return described
