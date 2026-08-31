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
"""

from __future__ import annotations

import logging
from typing import Any

from agents import (
    Agent,
    ModelSettings,
    OpenAIChatCompletionsModel,
    RunConfig,
    Runner,
    set_tracing_disabled,
)
from openai import AsyncOpenAI

from friday.config import AgentConfig
from friday.llm_log import LogHooks
from friday.redact import scrub

__all__ = ["Harness"]

log = logging.getLogger(__name__)

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

    async def run(
        self,
        prompt: str,
        *,
        context: Any = None,
        calls: list | None = None,
        extra_turns: int = 0,
    ) -> Any | None:
        """Run it. `None` means it did not answer.

        Every agent turns that into its own kind of work — a task for a human,
        or a fall back to a template — so none of them has to catch anything.
        The reason is kept in `last_error`, scrubbed, because it is stored
        against a task and a provider exception can quote an Authorization
        header.

        `extra_turns` is for an agent whose answer arrives as a tool call: the
        call and its result are two turns where a written answer is one.
        """
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
