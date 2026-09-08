from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from friday.triage.prompt import build_input, build_instructions
from friday.config import AgentConfig
from friday.agent.harness import Harness, stop_when
from friday.domain.actions import Decided, NeedsHuman, TriageOutcome
from friday.domain.models import InboundEvent
from friday.tools.classify import TOOLS, ClassifyCapture
from friday.triage.prefilter import Sensitive

__all__ = ["Decided", "NeedsHuman", "Triage", "TriageOutcome"]

log = logging.getLogger(__name__)

#: The bare text, kept as an attribute because tests pin sentences in it.
#: Assembly — examples and all — lives in `friday.triage.prompt`.
INSTRUCTIONS = build_instructions()


class Triage:
    """Decides what a mention is. Performs no writes.

    `classify` takes the type as a closed-enum argument rather than being
    one tool per type: the type still comes from the model, but naming a
    fourth type is adding a `Params` class, not a fourth tool. Tool calling is
    used rather than a structured output type because some OpenAI-compatible
    providers reject `response_format: json_schema` outright.
    """

    def __init__(
        self,
        *,
        config: AgentConfig,
        model=None,
        examples: Sequence[tuple[str, str]] = (),
        sensitive: Sensitive | None = None,
        #: The skill library, if this install has one. Triage gets it like
        #: every other agent now (the operator's call, 2026-09-07) — the
        #: harness wires the four tools and grants the turn they need.
        skills=None,
        record=None,
        spent=None,
    ) -> None:
        #: Empty by default, which means nothing is held. An install that has
        #: not thought about this yet gets the behaviour it would have had
        #: without the feature, rather than a silent list of someone else's
        #: guesses about what is sensitive in their workplace.
        self._sensitive = sensitive or Sensitive(())
        # Examples are appended to the instructions rather than passed per
        # call: the instructions are the stable prefix, and a list that
        # changed per call would cost the cache hit on everything after it.
        # A mark made now therefore takes effect at the next start.
        from friday.agent.instruction_prompt import SkillMeta

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
            instructions=build_instructions(
                examples,
                skills_meta=skills_meta,
            ),
            skills=skills,
            tools=TOOLS,
            model=model,
            record=record,
            spent=spent,
            context_type=ClassifyCapture,
            model_settings={
                # Without a forced tool call the vaguest message — "the api is
                # wrong", the most common shape there is — produces no call at
                # all and the mention silently yields nothing.
                "tool_choice": "required",
            },
            # Stop when a classification has actually been *recorded*, not
            # when the first tool call produces an output. The difference is
            # a call the schema rejects: `stop_on_first_tool` made the SDK's
            # "try again with valid JSON" the run's final answer, so the one
            # party who could fix it never saw it, and a mention the model
            # very nearly classified became work for a person. The budget for
            # that correction is one turn — `max_turns` and the one
            # `run(extra_turns=1)` adds — after which it is a person's anyway.
            tool_use_behavior=stop_when(lambda capture: capture.decided is not None),
        )

    async def decide(
        self,
        event: InboundEvent,
        *,
        context: Sequence[InboundEvent] = (),
    ) -> TriageOutcome:
        """Decide what a message is. Writes nothing, here or anywhere.

        Not even the call that decided it: the harness hands that to whatever
        sink it was built with, which is the composition root's business. This
        used to take a `calls` list and the runner used to drain it, and that
        arrangement is what left the extractors, the summariser and the
        responder unrecorded — see D1.
        """
        held = self._sensitive.found(event.text)
        if held is not None:
            # Held, not dropped. The guarantee is that the model does not see
            # it, not that nobody does: this list contains words that appear in
            # ordinary reports — "token hết hạn rồi" is a bug — and skipping
            # them would be dropping real mentions on the strength of one word.
            #
            # The word is named; the message is not. It is the thing being
            # kept quiet.
            log.info(
                "holding %s for you: it mentions %r, so it was not sent to the "
                "model", event.provider_message_id, held,
            )
            return NeedsHuman(f"mentions {held!r} — not sent to the model")

        capture = ClassifyCapture()
        # One extra turn: the answer arrives as a tool call, which is the call
        # and its result where a written answer would be one turn.

        said = build_input(list(context) + [event])
        result = await self._run.run(
            said,
            context=capture,
            # One for a malformed classify call, plus whatever the skill
            # tools need — the harness knows whether it wired them, so the
            # number is asked for rather than assumed here.
            extra_turns=1,
            message_id=event.provider_message_id,
        )
        if result is None:
            return NeedsHuman(f"triage failed: {self._run.last_error}")

        if capture.decided is None:
            return NeedsHuman("triage produced no classification")
        decided = capture.decided
        log.info(
            "triaged %s: %s (%.2f)",
            event.provider_message_id,
            decided.type,
            decided.confidence,
        )
        return decided
