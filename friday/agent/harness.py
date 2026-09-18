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
import json
import logging
from dataclasses import dataclass, fields as dataclass_fields, replace
from typing import Any

from agents import (
    Agent,
    AgentHooks,
    FunctionTool,
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
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    InternalServerError,
    RateLimitError,
)

from pydantic import TypeAdapter

from friday.agent.structured import Unfit, describe, find_json, fits
from friday.config import AgentConfig
from friday.domain.models import FridayState
from friday.ops.redact import scrub

__all__ = [
    "Harness",
    "Refused",
    "Hooks",
    "MCPServer",
    "MCPServerSse",
    "MCPServerStdio",
    "ToolContext",
    "create_static_tool_filter",
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
    # Tell the hooks, because the SDK will not: this returns a string, so
    # `on_tool_end` sees an ordinary result and would record a failure as an
    # answer.
    hooks = getattr(getattr(ctx, "agent", None), "hooks", None)
    if hooks is not None and hasattr(hooks, "tool_failed"):
        hooks.tool_failed(getattr(ctx, "tool_call_id", ""))
    if isinstance(error, ModelBehaviorError):
        return default_tool_error_function(ctx, error)
    log.warning("tool failed: %s", scrub(str(error)))
    return "that tool is unavailable right now — carry on without it"


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
        #: What this agent has already spent today, as an async callable of
        #: the agent's name. Separate from `record` because they are different
        #: capabilities over the same table — one writes, one reads — and a
        #: test that cares about one should not have to supply the other.
        #:
        #: Only asked when a budget is configured, so an install that has not
        #: set one pays nothing for the ceiling it does not have.
        spent=None,
        model=None,
        #: The skill library this agent may reach, or `None` for one that may
        #: not. Given it, **the harness wires the four skill tools itself** —
        #: the operator's call, 2026-09-07, and the argument is that a thing
        #: every agent needs is not four lines every agent has to remember.
        #: Forgetting them was invisible: the agent simply never reached for
        #: anything, which reads as a model that did not think to rather than
        #: as a door nobody built.
        #:
        #: An empty library is the same as none: an agent told about a tool it
        #: does not have goes looking for it, which is the rule
        #: `clarification_system` and `memory_tool_system` already follow.
        skills=None,
        #: The shape this agent's answer has, when it has one — a dataclass.
        #:
        #: **This reverses D1's wording, and deliberately.** The spec says the
        #: method "takes the prompt, the shape, and the state". It takes the
        #: prompt and the state; the shape is declared here, because an
        #: agent's answer shape does not vary per call — the summariser always
        #: answers a `RoomSummary`, each extractor always its own type's
        #: `Params`. Declaring it here builds the answer tool, its terminator
        #: and its `tool_choice` exactly once instead of on every call, and
        #: puts the agent's contract where the agent is built. D1's substance
        #: — one method asks for a shape and hands back an instance of it — is
        #: unchanged.
        answers: type | None = None,
        context_type: type | None = None,
        **agent_options: Any,
    ) -> None:
        self._config = config
        self._record = record
        self._spent = spent
        self.last_error: str | None = None
        #: Set when this run did not happen at all, rather than happening and
        #: failing. `last_error` carries the same words; this is what lets a
        #: caller branch on it without reading them.
        self.refusal: str | None = None
        #: Set when the model answered and the answer did not fit `answers`:
        #: why, and which of the shape's fields said so. The same idiom as
        #: `refusal` one line up, and for the same reason — `last_error` says
        #: it in words, and a caller that has to branch should not be reading
        #: them.
        #:
        #: Triage is that caller, and it branches on the *field*: "the model
        #: named a task type that does not exist" and "the model wrote
        #: nonsense in the confidence" are one validation failure and two
        #: different things to tell an operator (D20). `Unfit.fields` is what
        #: makes that answerable without matching a substring against a
        #: sentence written for a model.
        #:
        #: **Read it immediately after `run_structured` returned `None`, and
        #: nowhere else.** That is the one window in which it means what its
        #: name says: `_settle` clears it as every run begins, a run that
        #: recovered on its correction turn clears it on the way out, and a
        #: turned-down call sets it from inside the tool. Read at any other
        #: time it answers a question about a run that is not the one you are
        #: asking about.
        self.unfit: Unfit | None = None
        #: **One run of this harness at a time**, taken by `_settle` for the
        #: whole of a run — the budget check, the provider, the record.
        #:
        #: The pool works tasks side by side (ticket 13), and one extractor
        #: per type and one responder serve every task, so two runs of one
        #: harness at once became the ordinary case. Two things here are per
        #: run and live on the instance: the hooks that build a run's
        #: `ModelCall` hang off the one shared agent, so a second run replaced
        #: the first's and its call was written down under the other task;
        #: and the three flags above, which a second run clears as it begins.
        #:
        #: **Serialised rather than made local**, because the flags are read by
        #: the caller *after* the run returns, and that is safe only because
        #: nothing awaits between `_settle` releasing this lock and the caller
        #: reading them — a run waiting for it cannot start until the loop
        #: turns. The cost is that two tasks of one type take turns at the
        #: model; tasks of different types, whose harnesses differ, still run
        #: side by side, which is what ticket 13 was for.
        self._one_run = asyncio.Lock()
        #: The catalogue, for whoever builds this agent's instructions. Read
        #: off the same object the tools came from, so the prompt and the
        #: tools cannot describe different skills.
        #:
        #: `catalogue()` rather than a names accessor, because that is what
        #: `SkillLibrary` actually offers — the first version of this called
        #: a `names()` a test stub had invented, which is the same fixture
        #: failure as a stub with fewer attributes than the real type: it
        #: passes, and it is describing itself.
        self.skills: list[str] = (
            list(skills.catalogue()) if skills is not None and len(skills) else []
        )
        #: The budget for the tools this harness wires itself, added to
        #: `max_turns` in `run`. Every agent here is `max_turns: 1`, so a
        #: `fetch_skill` without this spends the only turn and the agent
        #: never classifies, extracts or drafts — the mention lands in
        #: `needs_human` and the reason is invisible. Handing out the tools
        #: and not the turns is worse than handing out neither.
        #:
        #: Two, not one, and the second is the whole point of the split: an
        #: agent that recognises a catalogue line fetches and answers, but one
        #: that does not is told to search *and then* fetch. A budget of one
        #: funds only the easy path, so `search_skills` — the tool that exists
        #: for the case the catalogue does not cover — could be called and
        #: never acted on.
        #:
        #: Read by `run`, not by callers: a caller that has to remember to add
        #: it has three places to forget in, and one of them did. The harness
        #: owns its own wiring; it owns its own budget.
        self.tool_turns = 2 if self.skills else 0
        tools = list(tools or [])
        if self.skills:
            from friday.tools.describe_skill import describe_skill_tool
            from friday.tools.fetch_skill import fetch_skill_tool
            from friday.tools.read_skill_file import read_skill_file_tool
            from friday.tools.search_skills import search_skills_tool

            tools += [
                fetch_skill_tool(skills),
                search_skills_tool(skills),
                describe_skill_tool(skills),
                read_skill_file_tool(skills),
            ]

        #: What `run_structured` validates against and hands back. The tool
        #: below is how it arrives: measured against the configured provider,
        #: a tool call's arguments come back in their own protocol field —
        #: no `<think>` block, no code fence, `json.loads` succeeds — while
        #: every *written* answer from the same model carries all three.
        self.answers = answers
        if answers is not None:
            # **Two settings this agent owns, and a caller may override
            # neither.** They are the mechanism, not a default it dresses up:
            # the terminator is what knows that "answered" means an instance
            # rather than any tool output, and the forced call is what stops
            # the model writing prose instead. A caller passing either is
            # asking for something that cannot work, so it is refused rather
            # than quietly honoured.
            #
            # `tool_choice` was refused only against the *file* until a review
            # read the comment against the code: a caller's `model_settings`
            # splatted after this dict, so `tool_choice: "auto"` from a caller
            # won, and disabled the mechanism with nothing said. The asymmetry
            # was invisible because the two settings sit four lines apart.
            owned = [
                name
                for name in ("tool_use_behavior",)
                if name in agent_options
            ]
            if "tool_choice" in agent_options.get("model_settings", {}):
                owned.append("tool_choice")
            if owned:
                raise ValueError(
                    f"an agent that declares `answers=` owns "
                    f"{' and '.join(owned)}; passing one would silently "
                    f"disable the mechanism it was built around"
                )
            tools = [*tools, _answer_tool(answers, self._refused)]
            agent_options["tool_use_behavior"] = _answered(answers)
            # Without a forced call the model writes prose instead, which is
            # the path this exists to stop being the road. It is not a
            # guarantee — the same probe that measured the clean arguments
            # also measured the model answering outside a closed enum — which
            # is why `run_structured` still validates, and still reads a
            # written answer if one turns up anyway.
            #
            # **And it forces the first call only.** `Agent.reset_tool_choice`
            # defaults to `True`, so the SDK drops back to `auto` once a tool
            # has been called — which means the *correction* turn is not
            # forced. That is the SDK's own guard against a model that cannot
            # stop calling tools, it is the right default, and D3's "the model
            # is required to call it" is therefore true of the first turn and
            # not of the repair. The written-answer fallback is what covers
            # the difference, which is one more reason it is not dead code.
            agent_options["model_settings"] = {
                "tool_choice": "required",
                **agent_options.get("model_settings", {}),
            }

        agent_class = Agent[context_type] if context_type else Agent
        self.agent = agent_class(
            name=config.name,
            instructions=instructions,
            model=model or _chat_model(config),
            tools=tools or [],
            mcp_servers=mcp_servers or [],
            # **Merged, not two splats.** These were `**config.settings,
            # **model_settings` side by side, which is a `TypeError: got
            # multiple values` the moment one key appears in both — and
            # `answers=` put `tool_choice` in the second while `settings:` in
            # `config.yaml` can put it in the first. A crash at construction
            # is a boot loop, and the operator's only clue would be a keyword
            # name.
            #
            # **What this class wired itself wins over the file**, which is
            # the opposite of the usual direction and is deliberate for the
            # one key that collides: an `answers=` agent that does not force
            # its tool call writes prose instead, so a `tool_choice: auto` in
            # `config.yaml` would quietly disable the mechanism the agent was
            # built around. This comment claimed the other direction until a
            # review read it against the test that pins this one.
            model_settings=ModelSettings(
                **{**config.settings, **agent_options.pop("model_settings", {})}
            ),
            **agent_options,
        )

    def _refused(self, problem: Unfit) -> None:
        """The answer tool turned a call down, and why.

        **The tool has to tell the harness, because the run may not get to.** A
        model that answers wrongly twice overruns `max_turns`, the SDK raises,
        `_settle` turns that into a `last_error`, and `run_structured` returns
        `None` having never seen a reply to check — so the only thing that
        knows the answer was *refused* rather than *absent* is the tool body
        that refused it. Without this, an invented task type and a provider
        outage arrive at the caller identically, which is exactly the pair D20
        exists to keep apart.

        On the instance, beside `last_error` and `refusal`, and safe there for
        the same reason they are: `_settle` lets one run of a harness happen
        at a time, and the caller reads the flag before anything awaits.
        """
        self.unfit = problem

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

    @property
    def tools(self) -> list:
        """Every tool this agent can reach, including the ones this class
        wired itself — the four skill tools, and the answer tool an
        `answers=` agent finishes through.

        Same reason as the two properties above: composition is worth
        checking, and the alternative is a test reaching through here into
        the SDK's own object to do it, which is the reach this module's seam
        rule exists to keep cheap to break. Two tests did exactly that before
        this property existed.
        """
        return list(self.agent.tools)

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

        **A caller passing a `FridayState` as `context` need not name the
        message or the task at all** (D8): the state already knows both, and
        `_About.of` reads them off it. An explicit argument still wins, for a
        caller that knows better than the state it was handed. `node` stays
        explicit because it is not a fact about the message — it is which step
        of a graph asked.
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

        Returns an instance of `answers`, or `None` — and `None` means the
        same thing it means from `run()`: nobody got an answer, the caller
        turns that into its own kind of work. It never means "an answer with
        nothing in it", which is exactly the confusion this method exists to
        end. The parser it replaced returned `{}` for output it could not
        read, every field of a `Params` has a default, and so an unreadable
        reply arrived at the caller as a *successful* extraction of nothing.

        **The answer arrives as a tool call, and that is measured rather than
        preferred** (D3). Probed against the configured provider on
        2026-09-11: a written answer always carries a `<think>` block, a
        ```json fence and prose after it, so `json.loads` on the reply fails
        every time; a tool call's arguments arrive in
        `tool_calls[].function.arguments` — clean, and a `curl` carrying
        `{"a":1}` came back byte for byte. Two different fields, and only one
        of them needs reading.

        **Nothing is sent on the wire to enforce the shape.** Not
        `response_format: json_schema`: the same provider accepts it and then
        ignores it, which is worse than rejecting it — a schema nobody
        enforces reads exactly like a schema somebody does. The tool's
        parameter schema goes out because that is how a tool is declared, and
        nothing relies on the provider honouring it. What makes this safe is
        that the arguments are validated **in this process**, every time.

        **One correction, and it is the turn budget** (D4). A call that does
        not fit comes back to the model as the tool's own output, naming the
        field — so the model can fix it inside the same run. `max_turns`
        decides how many goes it gets, which is the mechanism the SDK already
        applies to every other tool and the one CLAUDE.md documents. There is
        no second retry loop and, unlike the method this replaced, **no second
        run** — so a structured call is one `timeout_seconds`, not two, and
        the pool's worst case per task is what it always was.

        **A written answer is still read, and is the fallback rather than the
        road** (D13). Forcing the tool call is not a guarantee: the probe that
        found the clean arguments also found the model answering outside a
        closed enum when asked not to. If a reply turns up as text anyway,
        `friday/agent/structured.py` finds the object in it and checks it
        against the same shape — one path in, one check, whichever surface it
        came over.
        """
        if self.answers is None:
            raise ValueError(
                f"{self._config.name} was not built with `answers=`, so there "
                f"is no shape to ask for"
            )
        said = await self.run(
            prompt,
            context=context,
            # **This turn is the correction, and nothing else.** A run whose
            # answer fits ends on the first turn — the terminator finishes it
            # the moment the tool returns an instance, so the call and its
            # result are not two turns here the way they are for an agent
            # whose tools do not end the run. One turn answers; the one added
            # here is the single go at fixing a call that did not fit, which
            # is the budget CLAUDE.md states and the reason there is no retry
            # loop anywhere near this.
            #
            # Added here rather than asked of callers for the reason
            # `tool_turns` is: the harness is the only thing that knows how
            # its own answer arrives, and three callers remembering a number
            # is three places to forget. A caller that passes `extra_turns`
            # is asking for something beyond the correction.
            extra_turns=extra_turns + 1,
            message_id=message_id,
            task_id=task_id,
            node=node,
        )
        if said is None:
            return None

        answered = _instance_in(said, self.answers)
        if answered is not None:
            # A run that was corrected and then answered is not an unfit run.
            # `_refused` fires per turned-down call; clearing it here is what
            # makes the flag mean "this run produced no answer that fits"
            # rather than "a call was turned down somewhere along the way".
            self.unfit = None
            return answered

        # The written-answer fallback (D13), in two steps rather than one, so
        # the distinction the tool body draws is drawn here too: an object
        # that arrived and did not fit is the model naming something the shape
        # does not allow; no object at all is the model saying nothing.
        written = said.final_output
        data = find_json(written if isinstance(written, str) else "")
        if data is not None:
            value, problem = fits(data, self.answers)
            if problem is None:
                self.unfit = None
                return value
            self._refused(problem)
            self.last_error = (
                f"the answer did not fit {self.answers.__name__}: {problem.why}"
            )
        else:
            self.last_error = f"there was no {self.answers.__name__} in the reply"
        log.warning("%s: %s", self._config.name, self.last_error)
        return None

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

        One at a time per harness — see `_one_run`. The wait for it is outside
        `timeout_seconds`, which bounds the run and not the queue for it; a
        run waiting here waits at most for runs that are themselves bounded.
        """
        async with self._one_run:
            return await self._settle_alone(
                input_, context=context, max_turns=max_turns, about=about
            )

    async def _settle_alone(
        self,
        input_: Any,
        *,
        context: Any,
        max_turns: int,
        about: "_About | None" = None,
    ) -> Any | None:
        # Deferred: `llm_log` reaches `Hooks` through this module, so importing
        # it at module load time would be a cycle.
        from friday.agent.llm_log import LogHooks

        self.last_error = None
        self.refusal = None
        # Cleared here rather than in `run_structured`, which is where it was:
        # `_settle` is the one place every run begins, so a harness with an
        # `answers=` shape that is called through plain `run()` cannot read a
        # flag left by the run before it. No caller does that today, which is
        # exactly the condition under which a trap like this is laid.
        self.unfit = None
        if (refusal := await self._over_budget()) is not None:
            self.last_error = self.refusal = refusal
            log.warning("%s not called: %s", self._config.name, refusal)
            return None

        calls: list = []
        reached: list = []
        hooks = LogHooks(calls, model=self._config.model, tools=reached)
        self.agent.hooks = hooks
        progress = _Progress()
        try:
            # The timeout bounds the whole run, retries included, rather than
            # each try. The pool works a small, fixed number of tasks at once,
            # and one run of a harness at a time, so what has to stay bounded
            # is how long a run can hold a slot and this harness — three tries
            # at sixty seconds would be three minutes of every task behind it
            # waiting. The cost
            # is that a hiccup late in the budget leaves little room to try
            # again, which is the right way round: the run was already slow.
            return await asyncio.wait_for(
                self._attempts(input_, context, max_turns, hooks, calls, progress),
                timeout=self._config.timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001 - every failure becomes work
            self.last_error = _why(
                exc, self._config.timeout_seconds, progress.attempt
            )
            log.warning("%s failed: %s", self._config.name, self.last_error)
            return None
        finally:
            # In `finally` because a call that failed still cost what it cost.
            # A timeout cancels the run *inside* the provider request, so the
            # hook that builds a `ModelCall` never fires — and for every agent
            # configured `max_turns: 1`, which is triage, the extractors and
            # the summariser, that would mean no record at all of the call that
            # hung. `unfinished()` is what the run managed to send.
            # Whatever the last attempt sent and never got an answer to. On a
            # timeout that is the only record of the call that hung, and the
            # attempts that failed before it were flushed as they failed.
            progress.flush(hooks, calls)
            await self._write_down(calls + reached, about)

    async def _attempts(self, input_, context, max_turns, hooks, calls, progress):
        """Call the provider until it answers, it refuses in a way trying
        again cannot fix, or the attempts run out.

        Ours rather than the client's, and the client's is switched off in
        `_chat_model`. `AsyncOpenAI` retries by default and says nothing, so
        the provider bills three calls where the record holds one — and a
        record that disagrees with the invoice is the thing this board exists
        to stop. The cost is a dumber retry: doubling, and no reading of a
        `Retry-After` header.
        """
        last: Exception | None = None
        for attempt in range(1, self._config.max_attempts + 1):
            progress.attempt = attempt
            try:
                result = await Runner.run(
                    self.agent,
                    input_,
                    context=context,
                    max_turns=max_turns,
                    run_config=RunConfig(tracing_disabled=True),
                )
            except Exception as exc:  # noqa: BLE001 - decided by _transient
                # Whatever this attempt managed to send is a row of its own:
                # the provider charged for it, and the next attempt's hook
                # would otherwise overwrite the record of it.
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

        Before `Runner.run` rather than inside a hook, which is the whole of
        D2: `on_llm_start` fires once the decision to spend has been made and
        `on_llm_end` once the money is gone, and neither can refuse.

        A breach is a refusal and not a truncation. `max_tokens` bounds one
        answer; this bounds a day, and the difference matters because a
        shortened reply goes out under the operator's name while a refusal
        goes to the operator. Every other limit in this system routes to a
        person — low confidence, the turn cap, the sensitive-word prefilter —
        and this is that rule applied to money.
        """
        budget = self._config.daily_token_budget
        if budget is None or self._spent is None:
            return None
        try:
            spent = await self._spent(self._config.name)
        except Exception:  # noqa: BLE001 - this runs outside the clause below
            # Fails open, and the direction is a decision. This check sits
            # before `Runner.run` and so outside the `except` that turns every
            # other failure into a `last_error` — left bare it would be the one
            # path that raises past every caller, breaking the rule this module
            # is built on. Refusing instead would turn a store that cannot
            # answer one question into every agent refusing at once, and a
            # store in that state has already stopped the work by other means.
            log.exception("could not read %s's budget; going ahead", self._config.name)
            return None
        if spent < budget:
            return None
        return (
            f"{self._config.name} has spent {spent} of its {budget} tokens today"
        )

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

    @classmethod
    def of(
        cls,
        context: Any,
        *,
        message_id: str | None = None,
        task_id: int | None = None,
        node: str | None = None,
    ) -> "_About":
        """What this call was about, read off the run's state where there is
        one (D8) and named explicitly where there is not.

        The state already knows which message and which task a run is about —
        that is most of what it is for — so a caller carrying one should not
        have to say it again. `Pool._say` did exactly that: it built a state
        and then unpacked `state.task_id` straight back out to hand the
        harness separately.

        **`node` is never on the state**, because it is not a fact about the
        message. It is which step of a graph asked, which the graph knows and
        the journey does not.

        An explicit argument still wins, for a caller that knows better than
        the state it was handed — a run about a different message than the one
        the state carries, which is what `about_message` exists for on the
        other side of the same question.
        """
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


#: What is worth calling again. A list rather than a guess from the message:
#: a 400 is the provider saying the request itself is wrong, and paying to ask
#: it a second time buys nothing.
_TRANSIENT = (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError)


#: Statuses the SDK gives no class of its own, and that are still worth
#: another call. Only 408 today: the client maps every status at or above 500
#: to `InternalServerError` and 429 to `RateLimitError`, both already above —
#: so a `>= 500` test here, which is what this replaced, could never fire,
#: while 408 fell through it and was treated as the provider's final answer.
_RETRY_STATUSES = frozenset({408})


def _transient(exc: Exception) -> bool:
    if isinstance(exc, _TRANSIENT):
        return True
    return isinstance(exc, APIStatusError) and exc.status_code in _RETRY_STATUSES


@dataclass
class _Progress:
    """Which attempt is in flight, and how much of the record it has claimed.

    A value passed down rather than state on the `Harness`, and rather than
    the attempt number smuggled onto the exception, which is what the first
    version did. Both readers need it — the loop numbers rows as it goes, the
    `finally` numbers whatever a cancelled attempt left behind — so it is
    handed to both.
    """

    attempt: int = 1
    #: How many rows already carry a number. Everything after this belongs to
    #: the attempt in flight.
    claimed: int = 0

    def flush(self, hooks, calls: list) -> None:
        """Number every row this attempt produced, and keep what it sent.

        The hook builds a row when the provider answers; an attempt that
        failed leaves only what it sent, which `unfinished` returns.
        """
        if (cut_off := hooks.unfinished()) is not None:
            calls.append(cut_off)
        for index in range(self.claimed, len(calls)):
            calls[index] = replace(calls[index], attempt=self.attempt)
        self.claimed = len(calls)


def _why(exc: Exception, timeout: float, attempts: int = 0) -> str:
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
    if attempts > 1:
        # A person reads this. "429 slow down" alone reads as a moment; saying
        # how many times it was tried says the moment lasted, which is the
        # difference between something to ignore and something to look at.
        return f"gave up after {attempts} attempts: {scrub(str(exc))}"
    return scrub(str(exc))


def _chat_model(config: AgentConfig) -> OpenAIChatCompletionsModel:
    """Chat Completions rather than the Responses API, so `base_url`, `api_key`
    and `model` are the whole of what it takes to use a different
    OpenAI-compatible provider."""
    client = AsyncOpenAI(
        base_url=config.base_url,
        api_key=config.api_key,
        # Both chosen rather than inherited. The client's own default is ten
        # minutes and two silent retries: the first makes a run unbounded from
        # this side, and the second bills the provider three times for a call
        # the record counts once. Retrying is `_attempts`'s job, where it can
        # be seen and counted.
        #
        # A share of the run's budget rather than all of it. Given the whole
        # of it, the run-level timer always tripped first — and it cancels,
        # which is a `BaseException` the retry loop never sees — so a hung
        # provider spent the entire budget on one attempt and `APITimeoutError`
        # sat on the retry list unable to fire. The cost is that one
        # slow-but-working call now fails where it used to be waited out.
        timeout=config.timeout_seconds / max(config.max_attempts, 1),
        max_retries=0,
    )
    return OpenAIChatCompletionsModel(model=config.model, openai_client=client)


#: What the model calls to answer. One name for every shape, because an agent
#: built with `answers=` has exactly one way to finish, and a name that varied
#: per schema would be a second thing for a prompt to have to know.
ANSWER = "answer"


def _newest_instance(candidates, schema: type) -> Any | None:
    """The newest of `candidates` whose `output` is an instance of `schema`,
    and that **output** rather than the candidate holding it.

    Both places that need this scan newest-first for the same reason — a model
    may answer and reach for a skill in the same turn, and which order those
    arrive in is not ours to assume — so they share the scan rather than
    each carrying their own copy of the loop and their own chance to forget
    the direction.
    """
    for candidate in reversed(list(candidates or ())):
        output = getattr(candidate, "output", None)
        if isinstance(output, schema):
            return output
    return None


def _instance_in(result: Any, schema: type) -> Any | None:
    """The answer the tool built, taken off the run's own items.

    **Not `result.final_output`, and the reason is a live SDK behaviour worth
    writing down.** `tool_use_behavior` may hand back any object as the run's
    final output — and then the runner does
    `if agent.output_type is None or agent.output_type is str: final_output =
    str(final_output)`, so an instance arrives at the caller as its own
    `repr`. The fix the SDK intends is `output_type=schema`, which is the one
    thing this design may not do: that is what emits the
    `response_format: json_schema` envelope on every Chat Completions request
    (D5), and it would also make the SDK validate a *written* reply itself,
    raising where the fallback below wants to read it.

    So the instance is read where it actually is: the tool's own output item.
    Newest first, for the same reason `_answered` scans that way — a model may
    answer and reach for a skill in the same turn.
    """
    return _newest_instance(getattr(result, "new_items", ()), schema)


def _answer_tool(schema: type, refused) -> FunctionTool:
    """The tool an agent answers through, generated from the shape itself.

    **Not in `friday/tools/`,** and that is where it would belong if it were a
    capability. It is not: the tools in that package are doors an agent chooses
    among — reach a skill, search memory — and answering is not a choice. It is
    also unbuildable there, because `FunctionTool` comes from the SDK and this
    module is the only one allowed to import it. `tests/test_tools.py` names
    this exception out loud rather than letting its guard quietly skip a file.

    **The body returns its problem; it must not raise.** A raise inside
    `on_invoke_tool` is wrapped in `UserError` and fails the whole run, so the
    model never gets to correct the thing it could have corrected in a turn.
    Returning the reason puts it in the tool's output, which is where the model
    reads it. Found by prototype rather than by reading: the SDK's own
    `function_tool` catches exceptions for exactly this reason, and a
    hand-built `FunctionTool` has no such wrapper around it.

    **The arguments are not quoted back** in that reason, for the same reason
    the written-answer correction this replaced stopped quoting the reply: an
    extractor's whole job is copying a reporter's bytes out verbatim, so its
    own arguments are reporter-controlled text, and echoing them would walk
    that text back into the prompt outside the boundary the original put it
    behind. `fits` produces the reason inside this process, and it names the
    field.

    **`refused` is how the harness hears about a turned-down call**, and it
    exists because the run may never get back to tell it: a model that answers
    wrongly twice overruns `max_turns` and `run_structured` returns `None`
    having seen no reply at all. See `Harness._refused`.
    """

    async def invoke(ctx: Any, arguments: str) -> Any:
        # **Neither parse failure calls `refused`.** That signal means the
        # model *named* something the shape does not allow — a task type that
        # does not exist — and arguments that are not an object named nothing
        # at all. Counting the two together would put "the model invented a
        # label" and "the provider sent noise" in one number, which is the
        # pair D20 exists to keep apart.
        try:
            data = json.loads(arguments or "{}")
        except json.JSONDecodeError:
            return "that was not JSON — call it again with a JSON object"
        if not isinstance(data, dict):
            return "that was not a JSON object — call it again with one"
        value, problem = fits(data, schema)
        if problem is None:
            return value
        # Not optional, and not guarded. `NeedsHuman.out_of_set` is built from
        # this — it was a defaulted parameter with an `is not None` around
        # every call, which made a load-bearing contract read as a
        # convenience, and left the schema-only callers looking like they had
        # opted out of something. They want `_answer_params`, which is the
        # schema on its own.
        refused(problem)
        return f"that did not fit: {problem.why}"

    return FunctionTool(
        name=ANSWER,
        description=(
            f"Give your answer. Call this exactly once, with these fields:\n"
            f"{describe(schema)}"
        ),
        params_json_schema=_answer_params(schema),
        on_invoke_tool=invoke,
        # The provider ignores the schema either way — measured — so pinning
        # it strict buys nothing on the wire, and costs the one thing a strict
        # schema demands: every property required, which a shape whose fields
        # all have defaults cannot honestly say. What makes this safe is the
        # validation in `invoke`, which runs whatever the wire did.
        strict_json_schema=False,
    )


def _answer_params(schema: type) -> dict[str, Any]:
    """The tool's parameter schema, with each field's own `doc` on it.

    Pydantic reads a description off `Field(description=...)` and knows
    nothing about a dataclass field's `metadata`, which is where the meaning
    of a field lives here — the same string `describe` renders into the prompt
    and the extractor's own instructions read. Putting it on the parameter
    matters for the reason `classify`'s enum descriptions do: a tool parameter
    *is* an instruction to the model, and an undescribed one is an instruction
    to guess. `tests/test_tools.py` requires every field of every tool to carry
    a description; this is how these come to have one.
    """
    described = TypeAdapter(schema).json_schema()
    # The class's own docstring is developer prose — `RoomSummary`'s is nine
    # paragraphs of why it has four fields and not six — and pydantic puts it
    # on the object as `description`, which would ship it to the provider on
    # every call. What the model needs about the shape as a whole is on the
    # tool's description, generated from the fields; what it needs about a
    # field is on the field.
    described.pop("description", None)
    properties = described.get("properties", {})
    for field in dataclass_fields(schema):
        doc = field.metadata.get("doc")
        if doc and field.name in properties:
            properties[field.name].setdefault("description", doc)
    return described


def _answered(schema: type):
    """When a run carrying an answer tool is over.

    **"Answered" means the tool returned an instance, not that it returned
    anything** — the same distinction `stop_when` draws, for the same reason:
    the error string this tool hands back on a bad call is a tool output too,
    and `stop_on_first_tool` cannot tell it from a success. That mistake has
    already shipped here once, on triage: the SDK's "try again with valid
    JSON" became the run's final answer, and the one party who could act on it
    never saw it.

    Every result is checked, newest first, rather than only the last — a model
    may answer and reach for a skill in the same turn, and which order those
    arrive in is not ours to assume.
    """

    def decide(ctx, results) -> ToolsToFinalOutputResult:
        answered = _newest_instance(results, schema)
        if answered is None:
            return ToolsToFinalOutputResult(is_final_output=False)
        return ToolsToFinalOutputResult(is_final_output=True, final_output=answered)

    return decide
