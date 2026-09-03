from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from friday.triage.prompt import build_input, build_instructions
from friday.config import AgentConfig
from friday.agent.harness import Harness
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
        self._run = Harness(
            config=config,
            instructions=build_instructions(examples),
            tools=TOOLS,
            model=model,
            context_type=ClassifyCapture,
            model_settings={
                # Without a forced tool call the vaguest message — "the api is
                # wrong", the most common shape there is — produces no call at
                # all and the mention silently yields nothing.
                "tool_choice": "required",
            },
            tool_use_behavior="stop_on_first_tool",
        )

    async def decide(
        self,
        event: InboundEvent,
        *,
        context: Sequence[InboundEvent] = (),
        calls: list | None = None,
    ) -> TriageOutcome:
        """Decide what a message is. Writes nothing, here or anywhere.

        `calls` collects both sides of every model call, for a caller that
        wants to keep them — which is the runner, because storing is a write.
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
            said, context=capture, calls=calls, extra_turns=1
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
