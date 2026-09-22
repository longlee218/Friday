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
from typing import Any, cast

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

from friday.agent.structured import Unfit, describe, find_json, fits
from friday.config import AgentConfig
from friday.domain.models import FridayState

__all__ = [
    "Harness",
    "Hooks",
    "MCPToolset",
    "ModelRetry",
    "Refused",
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


class Refused(Exception):
    """We declined to make a call, rather than making one that failed.

    Two outcomes that both leave a caller with no answer, and they are worth
    telling apart. "The model could not answer" is worth asking the reporter
    for more; "we did not ask it" is worth telling the operator why, because
    nothing the reporter does will change it.

    `Harness` never raises this — the rule that no exception escapes it is
    older and more load-bearing than this distinction. It reports a refusal on
    `refusal` and a caller that has somewhere better to send it raises this
    itself; `friday/extraction` is the one that does, because the alternative
    there is asking a reporter for what they already wrote.
    """


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
    lives in the run's hooks (`friday/agent/llm_log.py`'s `tool_execute_error`),
    where it can also record the failure — the substitution and the record are
    one decision and belong together, and doing it per run rather than per tool
    is why this wrapper adds no `failure_error_function` of its own. A tool that
    wants the model to *fix* its call raises `ModelRetry` itself; anything else
    it raises is turned into the "unavailable" message there.
    """

    def make(fn):
        return Tool(fn, **options)

    return make(func) if func is not None else make


class Harness:
    """Builds an agent from configuration, and runs it."""

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
        #: What this agent has already spent today, as an async callable of the
        #: agent's name. Separate from `record` because they are different
        #: capabilities over the same table — one writes, one reads — and a test
        #: that cares about one should not have to supply the other. Only asked
        #: when a budget is configured, so an install that has not set one pays
        #: nothing for the ceiling it does not have.
        spent=None,
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
        context_type: type | None = None,
        **agent_options: Any,
    ) -> None:
        self._config = config
        self._record = record
        self._spent = spent
        self._instructions = instructions
        self.last_error: str | None = None
        #: Set when this run did not happen at all, rather than happening and
        #: failing. `last_error` carries the same words; this lets a caller
        #: branch on it without reading them.
        self.refusal: str | None = None
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
        #: The budget for the tools this harness wires itself, added to
        #: `max_turns` in `run`. Two, not one: an agent that recognises a
        #: catalogue line fetches and answers (one turn); one that does not is
        #: told to search *and then* fetch (two).
        self.tool_turns = 2 if self.skills else 0
        tool_list = list(tools or [])
        if self.skills:
            from friday.tools.describe_skill import describe_skill_tool
            from friday.tools.fetch_skill import fetch_skill_tool
            from friday.tools.read_skill_file import read_skill_file_tool
            from friday.tools.search_skills import search_skills_tool

            tool_list += [
                fetch_skill_tool(skills),
                search_skills_tool(skills),
                describe_skill_tool(skills),
                read_skill_file_tool(skills),
            ]
        self._tools = tool_list
        self._toolsets = list(mcp_servers or [])

        self.answers = answers
        output_type: Any = str
        retries: Any = None
        if answers is not None:
            # **Two things a caller may not override**, because they are the
            # mechanism, not a default: the output tool is what forces a
            # structured answer (Pydantic AI forces the output tool when no text
            # output is allowed), and the `{'output': 1}` retry is the one
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
            output_type = ToolOutput(
                self._answer_output(answers),
                name=ANSWER,
                description=(
                    f"Give your answer. Call this exactly once, with these "
                    f"fields:\n{describe(answers)}"
                ),
            )
            retries = {"output": 1}

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
        correction; `retries={'output': 1}` decides how many goes it gets.

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
        `last_error` and `refusal`, and safe there for the same reason: one run
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
        extra_turns: int = 0,
        message_id: str | None = None,
        task_id: int | None = None,
        node: str | None = None,
    ) -> Any | None:
        """Run it. `None` means it did not answer.

        Takes the user turn as a string; each family's own `prompt` module is
        what assembles it. Returns the run's result, whose text answer is
        `.output`; a non-answer is turned into `None` and a reason in
        `last_error`, scrubbed, so no caller has to catch anything.

        `extra_turns` is for an agent whose answer arrives as a tool call: the
        call and its result are two turns where a written answer is one.

        `message_id`, `task_id` and `node` are what this call was *about*. A
        caller supplies whichever it knows; a `FridayState` as `context` already
        knows the message and the task (D8), so a caller passing one need not
        name them.
        """
        return await self._settle(
            prompt,
            context=context,
            max_turns=self._config.max_turns + self.tool_turns + extra_turns,
            about=_About.of(
                context, message_id=message_id, task_id=task_id, node=node
            ),
        )

    async def run_structured(
        self,
        prompt: str,
        *,
        context: Any = None,
        extra_turns: int = 0,
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
        field, and `retries={'output': 1}` is the single go at fixing it. No
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
                extra_turns=extra_turns,
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
        # reason it did not answer — a provider failure, a timeout, a refusal,
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
        and written down (D2). One at a time per harness — see `_one_run`. The
        wait for it is outside `timeout_seconds`, which bounds the run and not
        the queue for it.
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
        from friday.agent.llm_log import LogHooks

        self.last_error = None
        self.refusal = None
        # Cleared here rather than in `run_structured`: `_settle` is the one
        # place every run begins, so a harness with an `answers=` shape called
        # through plain `run()` cannot read a flag left by the run before it.
        self.unfit = None
        if (refusal := await self._over_budget()) is not None:
            self.last_error = self.refusal = refusal
            log.warning("%s not called: %s", self._config.name, refusal)
            return None

        calls: list = []
        reached: list = []
        hooks = LogHooks(
            calls, model=self._config.model, tools=reached, agent=self._config.name
        )
        progress = _Progress()
        try:
            # The timeout bounds the whole run, retries included, rather than
            # each try: what has to stay bounded is how long a run can hold a
            # slot and this harness, not each attempt.
            return await asyncio.wait_for(
                self._attempts(prompt, context, max_turns, hooks, calls, progress),
                timeout=self._config.timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001 - every failure becomes work
            self.last_error = _why(
                exc, self._config.timeout_seconds, progress.attempt
            )
            log.warning("%s failed: %s", self._config.name, self.last_error)
            return None
        finally:
            # In `finally` because a call that failed still cost what it cost. A
            # timeout cancels the run inside the provider request, so the hook
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
        # Room for the one output correction on top of the turn budget, so the
        # request limit is never what stops a structured answer from being
        # fixed — that is the retry budget's job, and it is capped at one.
        request_limit = max_turns + (1 if self.answers is not None else 0)
        last: Exception | None = None
        for attempt in range(1, self._config.max_attempts + 1):
            progress.attempt = attempt
            try:
                result = await self.agent.run(
                    prompt,
                    deps=context,
                    usage_limits=UsageLimits(request_limit=request_limit),
                    capabilities=[hooks.capability],
                )
            except Exception as exc:  # noqa: BLE001 - decided by _transient
                progress.flush(hooks, calls)
                last = exc
                if not _transient(exc) or attempt == self._config.max_attempts:
                    raise
                log.info(
                    "%s: attempt %d of %d failed (%s), trying again",
                    self._config.name,
                    attempt,
                    self._config.max_attempts,
                    type(exc).__name__,
                )
                await asyncio.sleep(
                    self._config.retry_backoff_seconds * 2 ** (attempt - 1)
                )
            else:
                progress.flush(hooks, calls)
                return result
        raise last  # unreachable: the loop either returns or raises

    async def _over_budget(self) -> str | None:
        """Why this call is not being made, or `None` to make it.

        Before `agent.run` rather than inside a hook, which is the whole of D2:
        a hook fires once the decision to spend has been made. A breach is a
        refusal, not a truncation — it routes to a person the way every other
        limit here does.
        """
        budget = self._config.daily_token_budget
        if budget is None or self._spent is None:
            return None
        try:
            spent = await self._spent(self._config.name)
        except Exception:  # noqa: BLE001 - this runs outside the clause below
            # Fails open, and the direction is a decision: this check sits before
            # `agent.run` and so outside the `except` that turns every other
            # failure into a `last_error`. Left bare it would be the one path
            # that raises past every caller.
            log.exception(
                "could not read %s's budget; going ahead", self._config.name
            )
            return None
        if spent < budget:
            return None
        return f"{self._config.name} has spent {spent} of its {budget} tokens today"

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
_TRANSIENT = (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError)

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


def _why(exc: Exception, timeout: float, attempts: int = 0) -> str:
    """The reason, in a form somebody can act on.

    A timeout is the case this exists for: `asyncio.wait_for` raises a
    `TimeoutError` whose `str()` is empty. Everything else is scrubbed, because
    it is stored against a task and a provider exception can quote an
    Authorization header.
    """
    from friday.ops.redact import scrub

    if isinstance(exc, asyncio.TimeoutError):
        return f"no answer within {timeout:g}s"
    if attempts > 1:
        return f"gave up after {attempts} attempts: {scrub(str(exc))}"
    return scrub(str(exc))


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
    seen and counted) and its per-request timeout set to a share of the run's
    budget — given the whole of it, the run-level timer always tripped first,
    and it cancels, so a hung provider spent the entire budget on one attempt.
    """
    client = AsyncOpenAI(
        base_url=config.base_url,
        api_key=config.api_key,
        timeout=config.timeout_seconds / max(config.max_attempts, 1),
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
