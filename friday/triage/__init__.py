from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from friday.triage.context import build_light_context
from friday.triage.prompt import build_input, build_instructions
from friday.config import AgentConfig
from friday.agent.harness import Harness
from friday.domain.actions import Decided, NeedsHuman, TriageOutcome
from friday.domain.models import FridayState, InboundEvent
from friday.triage.prefilter import Sensitive

__all__ = ["Decided", "NeedsHuman", "Triage", "TriageOutcome"]

log = logging.getLogger(__name__)

#: The bare text, kept as an attribute because tests pin sentences in it.
#: Assembly — examples and all — lives in `friday.triage.prompt`.
INSTRUCTIONS = build_instructions()


class Triage:
    """Decides what a mention is. Performs no writes.

    **One answer, one closed set** (board `every-answer-has-a-shape`, D6). It
    answers a `Decided` — a member of `DECISIONS`, which is every task type
    plus `skip`, and a confidence — through the tool `Harness` generates from
    that shape, and the arguments are validated here before anything acts on
    them.

    It was two tools writing into a per-run capture the caller read back, so
    the classification was not the return value of anything and the two halves
    of one question were validated differently. An invented type could reach
    `TriageRunner._apply` and open a task the pool then discovers has no graph.
    """

    def __init__(
        self,
        *,
        config: AgentConfig,
        model=None,
        examples: Sequence[tuple[str, str]] = (),
        sensitive: Sensitive | None = None,
        record=None,
        spent=None,
        #: Where the room's summary lives. Held for the process, resolved
        #: per call — the same split `Extractor` uses (ticket 01), and for
        #: the same reason: a store outlives every call, a room does not.
        context=None,
    ) -> None:
        #: Empty by default, which means nothing is held. An install that has
        #: not thought about this yet gets the behaviour it would have had
        #: without the feature, rather than a silent list of someone else's
        #: guesses about what is sensitive in their workplace.
        self._sensitive = sensitive or Sensitive(())
        self._context = context
        # Examples are appended to the instructions rather than passed per
        # call: the instructions are the stable prefix, and a list that
        # changed per call would cost the cache hit on everything after it.
        # A mark made now therefore takes effect at the next start.
        self._run = Harness(
            config=config,
            instructions=build_instructions(examples),
            model=model,
            record=record,
            spent=spent,
            # The shape this agent answers. The harness generates the tool it
            # arrives through, forces the call, ends the run on an actual
            # `Decided` rather than on any tool output, and validates the
            # arguments — all four from this one class. Each of those was a
            # line here, and the last of them was a `stop_when` predicate over
            # a capture nobody could find from a signature.
            answers=Decided,
            context_type=FridayState,
        )

    async def decide(
        self,
        event: InboundEvent,
        *,
        #: The turn's own messages, raw — a burst of two or three, not one
        #: string joined ahead of time. Empty means render `event` alone,
        #: which is what every caller that has no turn to give (the eval, a
        #: bare unit test) already does today.
        turn: Sequence[InboundEvent] = (),
    ) -> TriageOutcome:
        """Decide what a message is. Writes nothing, here or anywhere.

        Not even the call that decided it: the harness hands that to whatever
        sink it was built with, which is the composition root's business. This
        used to take a `calls` list and the runner used to drain it, and that
        arrangement is what left the extractors, the summariser and the
        responder unrecorded — see D1.

        **This is the whole of ticket 09's change to this method**: `context:
        Sequence[InboundEvent]` — every message that had ever mentioned the
        operator in this conversation, unbounded and growing forever — is
        gone. What replaces it is the room's own summary, read from the
        context store this class now holds, plus `turn` in place of a
        pre-joined string.

        **Ticket 14 (D26):** this method no longer resolves the room itself.
        `build_light_context` is the one place that happens; this just calls
        it and renders from what it returns. `turn` still falls back to
        `[event]` here, not inside the builder — the builder takes a turn as
        given, and deciding what "no turn was given" should mean is this
        method's call, not a second rule about turns to remember.
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

        context = build_light_context(
            self._context,
            channel_id=event.conversation.channel_id,
            turn=list(turn) or [event],
        )
        said = build_input(context)
        # **No `extra_turns` here.** This line used to add one for a malformed
        # call, and `run_structured` now adds exactly that turn itself — so
        # asking again would buy a *second* correction on the highest-volume
        # path in the system, which is the budget CLAUDE.md pins at one for a
        # reason: a model that cannot get its own schema right twice will not
        # on the third go, and every attempt is billed.
        decided = await self._run.run_structured(
            said,
            # The run's state, which is what the SDK's context means now (D8).
            # This is where a message's journey starts, so this is where the
            # state is built; every later step takes the one it was handed.
            # It is also where the recording sink reads the message id from,
            # which is why nothing names it separately any more.
            context=FridayState.for_event(event, agent="triage"),
        )
        if decided is None:
            # **Two failures, told apart by a flag rather than by its words**
            # (D20). The model naming something outside the closed set is not
            # the provider being down: one says a prompt or a model is wrong,
            # the other says the network was, and counting them together would
            # hide the failure this change exists to make impossible.
            unfit = self._run.unfit
            return NeedsHuman(
                f"triage failed: {self._run.last_error}",
                # **`type` specifically, not "something did not fit"** (D20).
                # The eval prints this under "the model named a type that does
                # not exist", so it has to be true of every row it counts: a
                # confidence of `"very high"` is a formatting mistake, not an
                # invented decision, and reporting it as one would make the
                # number say something it does not mean. Found by review — the
                # flag fired on any validation failure at first.
                out_of_set=unfit is not None and "type" in unfit.fields,
            )
        log.info(
            "triaged %s: %s (%.2f)",
            event.provider_message_id,
            decided.type,
            decided.confidence,
        )
        return decided
