"""The second agent: saying it the way the operator would.

Tone comes from **examples of their real messages**, not from a description of
their style. A written style guide says "friendly, concise, uses Vietnamese";
eight of their actual replies say it far better, and they stay current without
anyone maintaining them.

Nothing this writes reaches a channel on its own. The fixed template was
allowed out unreviewed because it was the same sentence every time — that
argument does not survive a model writing it.

The plumbing here duplicates `friday.triage`: the client, the settings, the
hooks, the error policy. That duplication is the point of ticket 14, and it
could not be justified until a second agent existed to show what actually
varies. This is that agent.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass

from agents import Agent, ModelSettings, OpenAIChatCompletionsModel, RunConfig, Runner
from openai import AsyncOpenAI

from friday.config import AgentConfig
from friday.llm_log import LogHooks
from friday.models import InboundEvent

__all__ = ["Draft", "Responder"]

log = logging.getLogger(__name__)

INSTRUCTIONS = """You write chat replies as a specific backend engineer.

You are shown examples of how they actually write, the conversation so far, and
what needs to be said. Write that message the way they would write it.

Match their language, their length, and their register. If their examples are
in Vietnamese, reply in Vietnamese. They are usually brief.

Do not address anyone by @-mention. The message is posted as a reply to
theirs, so it is already attached to them.

Write only the message. No preamble, no quotes, no explanation."""


@dataclass(frozen=True, slots=True)
class Draft:
    """A reply, not yet sent, and not yet approved."""

    text: str


class Responder:
    def __init__(self, *, config: AgentConfig, model=None) -> None:
        self._config = config
        self._agent = Agent(
            name=config.name,
            instructions=INSTRUCTIONS,
            model=model or self._chat_model(config),
            model_settings=ModelSettings(**config.settings),
        )

    @staticmethod
    def _chat_model(config: AgentConfig) -> OpenAIChatCompletionsModel:
        client = AsyncOpenAI(base_url=config.base_url, api_key=config.api_key)
        return OpenAIChatCompletionsModel(model=config.model, openai_client=client)

    async def draft(
        self,
        *,
        asking: str,
        context: Sequence[InboundEvent] = (),
        tone: Sequence[InboundEvent] = (),
        calls: list | None = None,
    ) -> Draft | None:
        """Write what `asking` says, in the operator's voice.

        None when it could not — the caller falls back to the template. Never a
        reply in someone else's name that the model was unsure of, and never
        silence either.
        """
        self._agent.hooks = LogHooks(calls, model=self._config.model)
        try:
            result = await Runner.run(
                self._agent,
                _prompt(asking, context, tone),
                max_turns=self._config.max_turns,
                run_config=RunConfig(tracing_disabled=True),
            )
        except Exception as exc:  # noqa: BLE001 - the template still goes out
            log.warning("responder failed, falling back to the template: %s", exc)
            return None

        text = _without_reasoning(result.final_output or "")
        if not text:
            log.warning("responder returned nothing, falling back to the template")
            return None
        return Draft(text)


def _prompt(
    asking: str,
    context: Sequence[InboundEvent],
    tone: Sequence[InboundEvent],
) -> str:
    """Examples first, then the conversation, then the job — so the last thing
    the model reads is what it has to write."""
    lines = []
    if tone:
        lines.append("How this person writes:")
        lines += [f"  {m.text}" for m in tone]
        lines.append("")
    if context:
        lines.append("The conversation so far:")
        lines += [f"  {m.author_name}: {m.text}" for m in context]
        lines.append("")
    lines.append("Say this, in their voice:")
    lines.append(f"  {asking}")
    return "\n".join(lines)


#: Reasoning models put their working in the output. MiniMax M3 does; so do
#: several others, under the same tag. It is not part of the reply, and posting
#: it publishes the model's deliberation about the operator's colleagues under
#: the operator's own name.
_REASONING = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
#: A truncated response leaves the tag open. Everything after it is still
#: working, not an answer — so there is no reply in that response at all.
_UNCLOSED = re.compile(r"<think>.*", re.DOTALL | re.IGNORECASE)


def _without_reasoning(text: str) -> str:
    return _UNCLOSED.sub("", _REASONING.sub("", text)).strip()
