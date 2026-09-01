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
        # Persona, then the job, then what has been learned. All three are the
        # stable prefix — identity changes at a restart, instructions are
        # code, notes are promoted between runs — so the per-call prompt still
        # begins where the cache ends.
        #
        # Persona first because it is the same text for many agents: shared
        # bytes at the front of a prompt are the ones a provider's cache can
        # actually reuse across them.
        preamble = f"{config.persona}\n\n" if config.persona else ""
        self.agent = agent_class(
            name=config.name,
            instructions=preamble
            + (f"{instructions}\n\n{notes}" if notes else instructions),
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
        prompt: "str | ContextBundle",
        *,
        context: Any = None,
        calls: list | None = None,
        extra_turns: int = 0,
    ) -> Any | None:
        """Run it. `None` means it did not answer.

        Accepts a plain string (the scripted-test seam — unchanged) or a
        `ContextBundle` (ticket 27) whose `.render()` produces text appended
        to the user turn. The bundle is the one place every section of an
        agent's knowledge is assembled; this method does not assemble it.

        Note: a bundle is NOT a system prompt. The agent's role and tone
        live in `instructions` (set at `Harness.__init__`); the bundle
        contributes the per-call context. Mixing them here would put
        "You are triage" into the user turn, which is what the bundle
        was designed to avoid.

        Every agent turns that into its own kind of work — a task for a human,
        or a fall back to a template — so none of them has to catch anything.
        The reason is kept in `last_error`, scrubbed, because it is stored
        against a task and a provider exception can quote an Authorization
        header.

        `extra_turns` is for an agent whose answer arrives as a tool call: the
        call and its result are two turns where a written answer is one.
        """
        from friday.agent.instruction_prompt import ContextBundle

        if isinstance(prompt, ContextBundle):
            prompt = prompt.render()

        # Deferred: `llm_log` reaches `Hooks` through this module, so importing
        # it at module load time would be a cycle.
        from friday.agent.llm_log import LogHooks

        self.last_error = None
        self.agent.hooks = LogHooks(calls, model=self._config.model)
        try:
            return await Runner.run(
                self.agent,
                prompt,
                context=context,
                max_turns=self._config.max_turns + extra_turns,
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
