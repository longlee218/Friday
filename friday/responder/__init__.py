"""The second agent: saying it the way the operator would.

Tone comes from **examples of their real messages**, not from a description of
their style. A written style guide says "friendly, concise, uses Vietnamese";
eight of their actual replies say it far better, and they stay current without
anyone maintaining them.

Nothing this writes reaches a channel on its own. The fixed template was
allowed out unreviewed because it was the same sentence every time — that
argument does not survive a model writing it.

What it declares is only what makes it different from triage: instructions,
and what to do with the answer. The client, the settings, the hooks and the
error policy belong to `friday.harness` — this agent is the reason that module
exists.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass

from friday.config import AgentConfig
from friday.harness import Harness
from friday.skills import fetch_skill_tool
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
    def __init__(
        self,
        *,
        config: AgentConfig,
        model=None,
        notes: str = "",
        skills=None,
    ) -> None:
        #: The operator's written-down knowledge. The responder gets it
        #: because how they write to their team is exactly the kind of thing
        #: they write down — which technical words stay in English, how short
        #: is short enough. Triage does not: it stops on its first tool call
        #: by design, so a fetch there would end the run before it classified.
        self._skills = skills
        self._run = Harness(
            config=config,
            instructions=INSTRUCTIONS,
            model=model,
            notes=notes,
            tools=[fetch_skill_tool(skills)] if skills is not None else [],
        )

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
        message in someone else's name that the model was unsure of, and never
        silence either.
        """
        from datetime import datetime, timezone

        from friday.instruction_prompt import (
            ContextBundle,
            base,
            conversation,
            identity,
            task,
            tone_examples,
        )
        from friday.instruction_prompt import skills as skills_section

        bundle = ContextBundle(
            identity=identity(
                "responder",
                "You write chat replies as the watched account, in their voice.",
            ),
            base=base(datetime.now(timezone.utc)),
            skills=skills_section(
                self._skills.catalogue() if self._skills is not None else None
            ),
            tone=tone_examples(list(tone)),
            conversation=conversation(list(context)),
            task=task("respond", None, asking),
        )
        # Two extra turns when a skill can be fetched: the call and its
        # answer both land before the reply is started, and without the room
        # asking for a skill would mean never writing anything.
        result = await self._run.run(
            bundle, calls=calls, extra_turns=2 if self._skills is not None else 0
        )
        if result is None:
            log.warning("falling back to the template")
            return None

        text = _without_reasoning(result.final_output or "")
        if not text:
            log.warning("responder returned nothing, falling back to the template")
            return None
        return Draft(text)


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
