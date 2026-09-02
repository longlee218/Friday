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
error policy belong to `friday.agent.harness` — this agent is the reason that module
exists.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass

from friday.config import AgentConfig
from friday.agent.persona import Family
from friday.responder.prompt import build_input, build_instructions
from friday.agent.harness import Harness
from friday.agent.skills import fetch_skill_tool
from friday.domain.models import Params, InboundEvent

__all__ = ["Draft", "Responder"]

log = logging.getLogger(__name__)

#: The bare text; assembly lives in `friday.responder.prompt`.
INSTRUCTIONS = build_instructions()



@dataclass(frozen=True, slots=True)
class Draft:
    """A reply, not yet sent, and not yet approved."""

    text: str


class Responder:
    @classmethod
    def build(
        cls, config, *, notes: str = "", skills=None, context_store=None
    ) -> "Responder | None":
        """The responder, or None when it is off or unconfigured.

        `None` is a working state, not a failure: the workflow falls back to
        the plain template, which is what shipped before this existed.
        """
        settings = config.agents.get("responder")
        if not (config.workflows.use_responder and settings):
            return None
        built = cls(
            config=settings,
            notes=notes,
            skills=skills,
            context_store=context_store,
            persona=(
                config.persona.render(Family.RESPONDER)
                if getattr(config, "persona", None)
                else ""
            ),
        )
        log.info(
            "responder on %s — its drafts need approval before they go out",
            settings.model,
        )
        return built

    def __init__(
        self,
        *,
        config: AgentConfig,
        model=None,
        notes: str = "",
        skills=None,
        persona: str = "",
        context_store=None,
    ) -> None:
        #: Where the room's register lives. None means every channel writes
        #: the way the persona alone says, which is what shipped before.
        self._context = context_store
        #: How many of the operator's real messages to show as tone examples.
        #: The responder's knob, read where the responder is built — the
        #: workflow runner fetches them but has no opinion on how many.
        self.tone_examples = int(config.options.get("tone_examples", 8))
        #: The operator's written-down knowledge. The responder gets it
        #: because how they write to their team is exactly the kind of thing
        #: they write down — which technical words stay in English, how short
        #: is short enough. Triage does not: it stops on its first tool call
        #: by design, so a fetch there would end the run before it classified.
        self._skills = skills
        self._run = Harness(
            config=config,
            instructions=build_instructions(persona),
            model=model,
            notes=notes,
            tools=[fetch_skill_tool(skills)] if skills is not None else [],
        )

    def knows(self, channel_id: str, name: str) -> bool:
        """Whether the operator wrote this person down for this room."""
        room = self._context.context(channel_id) if self._context else None
        people = (room.overrides.get("people") or {}) if room else {}
        return name in people

    async def draft(
        self,
        *,
        asking: str,
        params: "Params | None" = None,
        channel_id: str | None = None,
        stranger: bool = False,
        context: Sequence[InboundEvent] = (),
        tone: Sequence[InboundEvent] = (),
        calls: list | None = None,
    ) -> Draft | None:
        """Write what `asking` says, in the operator's voice.

        `params` is what this task actually knows, and it is not optional in
        spirit. Without it the model has only the conversation to go on — and a
        conversation is a whole channel, which may hold another task's
        correlationId. Asked to request one, it read the channel, found one
        that belonged to a different report, and wrote "ok có correlationId
        rồi, để anh trace thử": false, promising work nobody would do, and sent
        under the operator's name with no approval step.

        None when it could not write — the caller falls back to the template.
        Never a message in someone else's name that the model was unsure of,
        and never silence either.
        """
        room = (
            self._context.context(channel_id)
            if self._context is not None and channel_id is not None
            else None
        )
        bundle = build_input(
            asking=asking,
            params=params,
            room=room,
            stranger=stranger,
            skills_catalogue=(
                self._skills.catalogue() if self._skills is not None else None
            ),
            tone=tone,
            context=context,
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
