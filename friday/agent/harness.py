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

import logging
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
    function_tool,
    set_tracing_disabled,
)
from agents.mcp import (
    MCPServer,
    MCPServerSse,
    MCPServerStdio,
    create_static_tool_filter,
)
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
    "tool",
]

log = logging.getLogger(__name__)

#: What a tool implementation needs from the SDK, under a name that does not
#: name it. `tool` decorates a function; `ToolContext` types its first
#: argument; `Hooks` is the base a logging or tracing hook subclasses.
tool = function_tool
ToolContext = RunContextWrapper
Hooks = AgentHooks

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
        #: What has been learned across earlier tasks. Appended to the
        #: instructions rather than to the prompt, because that is the
        #: stable early part — a byte that moves there costs a cache hit on
        #: everything after it.
        notes: str = "",
        model=None,
        context_type: type | None = None,
        **agent_options: Any,
    ) -> None:
        self._config = config
        self.last_error: str | None = None
        agent_class = Agent[context_type] if context_type else Agent
        self.agent = agent_class(
            name=config.name,
            instructions=f"{instructions}\n\n{notes}" if notes else instructions,
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
        calls: list | None = None,
        extra_turns: int = 0,
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
        """
        return await self._settle(
            self.agent,
            prompt,
            context=context,
            calls=calls,
            max_turns=self._config.max_turns + extra_turns,
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
        calls: list | None = None,
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
            self.agent, state, context=None, calls=calls, max_turns=self._config.max_turns
        )

    async def _settle(
        self, agent, input_: Any, *, context: Any, calls: list | None, max_turns: int
    ) -> Any | None:
        """Run to completion or to the first thing that stops it, and turn a
        failure into `last_error` rather than an exception every caller would
        otherwise have to catch identically."""
        # Deferred: `llm_log` reaches `Hooks` through this module, so importing
        # it at module load time would be a cycle.
        from friday.agent.llm_log import LogHooks

        self.last_error = None
        agent.hooks = LogHooks(calls, model=self._config.model)
        try:
            return await Runner.run(
                agent,
                input_,
                context=context,
                max_turns=max_turns,
                run_config=RunConfig(tracing_disabled=True),
            )
        except Exception as exc:  # noqa: BLE001 - every failure becomes work
            self.last_error = scrub(str(exc))
            log.warning("%s failed: %s", self._config.name, self.last_error)
            return None


def _chat_model(config: AgentConfig) -> OpenAIChatCompletionsModel:
    """Chat Completions rather than the Responses API, so `base_url`, `api_key`
    and `model` are the whole of what it takes to use a different
    OpenAI-compatible provider."""
    client = AsyncOpenAI(base_url=config.base_url, api_key=config.api_key)
    return OpenAIChatCompletionsModel(model=config.model, openai_client=client)
