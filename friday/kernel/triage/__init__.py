from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from friday.kernel.triage.context import build_light_context
from friday.kernel.triage.prompt import build_input, build_instructions
from friday.kernel.config import AgentConfig
from friday.sdk.agent import AgentDeclaration
from friday.kernel.harness.harness import Harness
from friday.kernel.domain.triage import Decided, NeedsHuman, TriageOutcome, make_decided
from friday.kernel.domain.models import FridayState, InboundEvent
from friday.kernel.triage.prefilter import Sensitive

__all__ = ["Decided", "NeedsHuman", "Triage", "TriageOutcome"]

log = logging.getLogger(__name__)

#: The bare text, kept as an attribute because tests pin sentences in it.
#: Assembly — examples and all — lives in `friday.kernel.triage.prompt`.
INSTRUCTIONS = build_instructions()

#: Runs on every mention — the firehose — so the cheapest tool-capable tier.
#: One turn: the forced answer is the whole run (its one correction is an
#: attempt, not a turn). Changing the tier needs `run_triage_eval` first.
#: 30s a request: the slowest measured was 16.9s (live `model_calls`,
#: 2026-09-28).
TRIAGE = AgentDeclaration(
    name="triage", tier="flash", temperature=0.0, max_turns=1, tokens=50_000,
    request_timeout_seconds=30.0,
)


class Triage:
    """Decides what a mention is. Performs no writes.

    **One answer, one closed set** (board `every-answer-has-a-shape`, D6). It
    answers a `Decided` — a registered task type plus `skip`, and a confidence
    — through the tool `Harness` generates from the boot schema `make_decided`
    builds from the registry, and the arguments are validated there before
    anything acts on them (ticket 11).

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
        #: The `task_type -> Params` mapping the classifier's closed set is built
        #: from (ticket 11). The registry fills this at boot; `None` falls back
        #: to the current task-type catalog, so a bare test needs no registry.
        decisions: Mapping[str, type] | None = None,
        #: Where the room's summary row is read from — anything with
        #: `room_summary(channel_id)`, which is the store in production. Held
        #: for the process, read per call: a store outlives every call, a
        #: room's summary does not. Named for what it is asked, not `db`,
        #: because triage writes nothing and a whole store would say it
        #: could. `None` reads no room at all — a bare test.
        summaries=None,
    ) -> None:
        #: Empty by default, which means nothing is held. An install that has
        #: not thought about this yet gets the behaviour it would have had
        #: without the feature, rather than a silent list of someone else's
        #: guesses about what is sensitive in their workplace.
        self._sensitive = sensitive or Sensitive(())
        self._summaries = summaries
        self._decisions = decisions
        # Examples are appended to the instructions rather than passed per
        # call: the instructions are the stable prefix, and a list that
        # changed per call would cost the cache hit on everything after it.
        # A mark made now therefore takes effect at the next start.
        self._run = Harness(
            config=config,
            instructions=build_instructions(examples),
            model=model,
            record=record,
            # The shape this agent answers, built at boot from the registry's
            # task types (ticket 11): `type` closed to those plus `skip`. The
            # harness generates the tool it arrives through, forces the call,
            # ends on an actual answer of this shape and validates it — so a type
            # outside the set is refused here, before anything acts on it,
            # exactly as the old static `Literal` did.
            answers=self._answer_type,
            context_type=FridayState,
        )

    @property
    def _answer_type(self) -> type:
        decisions = self._decisions
        if decisions is None:
            # The registry is the catalog: whatever task types are registered
            # (the composition root fills it at boot; the autouse test fixture
            # fills it for a bare `Triage`). An explicit `decisions` overrides,
            # for a test that wants a specific set.
            from friday.kernel.dag import registry

            decisions = registry.decision_params()
        return make_decided(decisions)

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
        gone. What replaces it is the room's own summary row, read from the
        store this class holds, plus `turn` in place of a pre-joined string.

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

        context = await build_light_context(
            self._summaries,
            channel_id=event.conversation.channel_id,
            turn=list(turn) or [event],
        )
        said = build_input(context)
        # **No extra turn here.** This line used to add one for a malformed
        # call, and `run_structured` now adds exactly that correction itself
        # (`OUTPUT_CORRECTIONS`) — so asking again would buy a *second* one on
        # the highest-volume path in the system, which is the budget CLAUDE.md
        # pins at one for a reason:a model that cannot get its own schema right twice will not
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
                # **A type the model *sent* and this shape refused** (D20) —
                # not any failure, and not any failure of the `type` field.
                # The eval prints this under "the model named a type that does
                # not exist", so every row it counts has to be one: a
                # confidence of `"very high"` is a formatting mistake, an
                # empty answer names nothing, and a real type sent under the
                # deleted tool's old key names nothing this shape can see.
                # Each of those three was counted here at some point, and each
                # was found by review rather than by the suite.
                out_of_set=unfit is not None and "type" in unfit.rejected,
            )
        log.info(
            "triaged %s: %s (%.2f)",
            event.provider_message_id,
            decided.type,
            decided.confidence,
        )
        # `decided` is an instance of the boot-built schema (its `type` already
        # validated against the closed set); the rest of the system speaks in
        # `Decided` values, so hand back one.
        return Decided(type=decided.type, confidence=decided.confidence)
