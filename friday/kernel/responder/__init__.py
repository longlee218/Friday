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
error policy belong to `friday.kernel.harness.harness` — this agent is the reason that module
exists.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from friday.kernel.config import AgentConfig
from friday.kernel.domain.messages import InboundEvent
from friday.kernel.domain.state import FridayState
from friday.kernel.harness.harness import Harness
from friday.kernel.responder.prompt import (
    build_instructions,
    build_reply_input,
)
from friday.kernel.toolsets.memory import memory_tools
from friday.sdk.agent import AgentDeclaration
from friday.sdk.intake import IntakeContext

__all__ = ["Draft", "Responder"]

log = logging.getLogger(__name__)

#: The bare text; assembly lives in `friday.kernel.responder.prompt`.
INSTRUCTIONS = build_instructions()

#: Writes prose that goes out under the operator's name. Thirteen turns, what
#: it had when tool turns were added on top: the answer, two for the skill
#: tools (search, then fetch) and two for each of the five memory tools. A
#: ceiling, not a target — it costs nothing to a run that answers in one turn.
#: 60s a request: it writes prose, and the slowest measured was 31s.
RESPONDER = AgentDeclaration(
    name="responder",
    tier="flash",
    temperature=0.7,
    max_turns=13,
    tokens=300_000,
    request_timeout_seconds=60.0,
)
#: How many of the operator's real messages to show as tone examples.
TONE_EXAMPLES = 8


@dataclass(frozen=True, slots=True)
class Draft:
    """A reply, not yet sent, and not yet approved."""

    text: str


class Responder:
    @classmethod
    def build(
        cls,
        config,
        *,
        skills=None,
        db=None,
        record=None,
    ) -> Responder | None:
        """The responder, or None when it is off or unconfigured.

        `None` is a working state, not a failure: the workflow falls back to
        the plain template, which is what shipped before this existed.
        """
        if not config.workflows.use_responder:
            return None
        settings = config.agent(RESPONDER)
        built = cls(
            config=settings,
            skills=skills,
            db=db,
            record=record,
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
        skills=None,
        #: The store, for the memory tools and for the room's summary row — the responder's own, scoped
        #: to one channel per call. `None` means what it means for `skills`:
        #: the tools are not wired and the prompt does not claim them.
        #: Ticket 09's D9: the responder is the obvious first agent to get
        #: these, since it is the one that writes text a person reads and
        #: what a room likes is exactly the kind of thing worth remembering.
        db=None,
        record=None,
    ) -> None:
        #: Where the room's summary row is read from, per draft. It was a
        #: `ContextStore` of YAML files, whose `overrides` could carry a
        #: room's `register` and a `people:` map; the files are gone (board
        #: `read-it-the-way-the-operator-does`, ticket 10). How a room is
        #: spoken in is a `voice` row now, reached through `memory_search`,
        #: and who someone is is a `person` row, read by code.
        self._db = db
        #: How many of the operator's real messages to show as tone examples.
        #: The responder's knob, read where the responder is built — the
        #: workflow runner fetches them but has no opinion on how many.
        self.tone_examples = TONE_EXAMPLES
        #: The operator's written-down knowledge. The responder gets it
        #: because how they write to their team is exactly the kind of thing
        #: they write down — which technical words stay in English, how short
        #: is short enough. Triage does not: it stops on its first tool call
        #: by design, so a fetch there would end the run before it classified.
        self._skills = skills
        #: The four ways it can reach a skill: read one it named, find one it
        #: could not name, look at one before reading it, follow a link out of
        #: a body. Kept as a local rather than read back off the agent, because
        #: `harness.py` is the only module that may know the SDK's shape — the
        #: `Harness` properties exist so nobody reaches through it, and the
        #: turn budget below is the one caller that wanted to.
        #: **Non-empty**, not merely present. A library with nothing in it
        #: gives four tools that can only answer "none are defined", and it
        #: used to: the tools were wired on `skills is not None` while the
        #: prompt described them only when the catalogue had lines, so a fresh
        #: install handed the agent three tools it was never told about. The
        #: two facts agree now because they are read off the same one, which
        #: is what lets `build_reply_input` keep deciding from the catalogue alone.
        #: Only the memory tools are wired here now. The four skill tools
        #: moved into `Harness`, which hands them to any agent given a
        #: library — the operator's call, 2026-09-07: four lines every agent
        #: has to remember is four lines every agent can forget, and
        #: forgetting them looks like a model that did not think to reach.
        #:
        #: Given only when there is a store to back them, and `build_reply_input`'s
        #: `has_memory` reads this same fact rather than a second flag that
        #: could drift from it.
        self._has_memory = db is not None
        tools = memory_tools(db) if self._has_memory else []
        # The catalogue goes in the stable half now, so it is built here
        # rather than on every call. Same fact as the tools below. Built
        # from the same library the harness is about to wire tools from,
        # so the catalogue in the prompt and the tools in the agent
        # cannot describe different skills.
        from friday.kernel.harness.instruction_prompt import SkillMeta

        skills_meta = None
        if skills is not None and len(skills):
            skills_meta = [
                SkillMeta(
                    name=s.name,
                    description=s.description,
                    mutability=s.mutability,
                    location=str(skills.location_of(s.name)),
                    allowed_tools=s.allowed_tools,
                )
                for s in skills.skills()
            ]
        self._run = Harness(
            config=config,
            instructions=build_instructions(skills_meta=skills_meta),
            skills=skills,
            model=model,
            tools=tools,
            # **Unconditional, where it used to follow `_has_memory`.** The
            # state travels whether this agent has memory tools or not — see
            # `reply`, which says why — so a context type that appeared only
            # with the tools was the last place the slot meant two things
            # depending on how the agent was built.
            context_type=FridayState,
            record=record,
        )

    async def reply(
        self,
        *,
        action: str,
        intake: IntakeContext,
        reads: Mapping[str, Any],
        state: FridayState | None = None,
        stranger: bool = False,
        context: Sequence[InboundEvent] = (),
        tone: Sequence[InboundEvent] = (),
    ) -> Draft | None:
        """The spine's `draft` step (build-the-spine ticket 14): the reply to
        the reporter, from what the steps it reads came to and the intake
        context — the no-invention rule over both (`prompt.REPLYING`).
        `None` when it could not write; there is no template to fall back
        on, so the step fails."""
        channel_id = state.channel_id if state is not None else None
        summary = (
            await self._db.room_summary(channel_id)
            if self._db is not None and channel_id is not None
            else None
        )
        said = build_reply_input(
            action=action,
            intake=intake,
            reads=reads,
            summary=summary,
            stranger=stranger,
            has_memory=self._has_memory,
            tone=tone,
            context=context,
        )
        return await self._write(said, state)

    async def _write(self, said: str, state: FridayState | None) -> Draft | None:
        # **The state travels whether or not this agent has memory tools**: the
        # recording sink reads the message and the task off this same context
        # (D8), so gating it on the tools cost every memory-less responder its
        # own correlation. `None` only when there is no state at all, which is
        # a test and not a real task.
        #
        # **`as_agent` rather than the state as handed in**, and it is a
        # guarantee: a memory's provenance is "who wrote this, and while doing
        # what", and this is where the answer is known for certain — the state
        # travels a whole message's journey, and triage is at the front of it.
        scope = state.as_agent("responder") if state is not None else None
        # No `task_id=` here: the state carries it, and `_About.of` reads it
        # off the context (D8). Naming it again was the state being unpacked
        # one line after being bundled.
        result = await self._run.run(said, context=scope)
        if result is None:
            log.warning("the responder wrote nothing")
            return None

        text = _without_reasoning(result.output or "")
        if not text:
            log.warning("the responder returned an empty draft")
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
