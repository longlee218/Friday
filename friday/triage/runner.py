"""Applying triage decisions.

Deliberately separate from deciding them. Triage performs no writes, so it is
testable without a database; this is testable without a model. The seam between
them is a decision object.

This also runs off a queue rather than inside the ingest loop. A model call in
that loop would stall the gateway consumer for its duration — the exact failure
the recovery layer exists to prevent, self-inflicted.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace

from friday.store.db import Database
from friday.domain.tasks import TaskState
from friday.domain.models import InboundEvent, Task
from friday.triage import Decided, NeedsHuman, TriageOutcome

__all__ = ["PENDING", "NEEDS_HUMAN", "TriageRunner"]

log = logging.getLogger(__name__)

PENDING = TaskState.PENDING
NEEDS_HUMAN = TaskState.NEEDS_HUMAN
ASKED = TaskState.WAITING_FOR_DETAILS


def _record(outcome: TriageOutcome) -> dict:
    """What to keep about a decision, whether or not it opened a task.

    Skips and follow-ups are absorbed elsewhere and would otherwise vanish, and
    they are exactly the decisions a threshold has to be checked against.
    """
    if isinstance(outcome, NeedsHuman):
        return {
            "type": NEEDS_HUMAN,
            "confidence": 0.0,
            "params": {"reason": outcome.reason},
        }
    # No parameters: triage classifies and stops. The task opens empty and the
    # extractor fills it from what the reporter wrote.
    return {"type": outcome.type, "confidence": outcome.confidence, "params": {}}


class TriageRunner:
    @classmethod
    async def build(cls, config, *, db: Database) -> "TriageRunner":
        """Everything triage needs, read from configuration here.

        The composition root asks for a triage runner; it does not know that
        triage has a confidence threshold, or that it shows the classifier
        examples, or how many. Those are this step's knobs and this is where
        they are read — the same shape `register_extractors` and
        `register_dags` already use, for the same reason: adding a knob is a
        change here, not there.
        """
        from friday.triage import Triage
        from friday.triage.prefilter import Sensitive

        try:
            settings = config.agents["triage"]
        except KeyError:
            raise SystemExit(
                "No 'triage' agent in config.yaml — see the agents section."
            ) from None

        # Read once, at build time. Examples belong in the stable front of the
        # prompt, and a list that changed per call would cost the cache hit on
        # everything after it — a mark made now takes effect at the next start.
        examples = list(config.triage_examples) + await db.confirmed_classifications(
            limit=int(settings.options.get("examples", 8))
        )
        if examples:
            log.info("triage: %d example(s) the operator vouched for", len(examples))

        log.info("triage on %s via %s", settings.model, settings.base_url)

        sensitive = Sensitive(config.sensitive_words)
        if len(sensitive):
            log.info(
                "%d word(s) keep a message away from the model — it is held "
                "for you instead", len(sensitive),
            )
        else:
            log.warning(
                "sensitive_words is empty — every message goes to %s, "
                "including anything about pay, health or credentials",
                settings.base_url,
            )

        return cls(
            db=db,
            triage=Triage(
                config=settings, examples=examples, sensitive=sensitive
            ),
            confidence_threshold=float(
                settings.options.get("confidence_threshold", 0.7)
            ),
        )

    def __init__(
        self,
        *,
        db: Database,
        triage,
        confidence_threshold: float,
        batch_size: int = 50,
        poll_interval_seconds: float = 2.0,
    ) -> None:
        self._db = db
        self._triage = triage
        self._threshold = confidence_threshold
        self._batch_size = batch_size
        self._poll_interval = poll_interval_seconds

    async def run_forever(self) -> None:
        while True:
            if not await self.run_once():
                await asyncio.sleep(self._poll_interval)

    async def run_once(self) -> list[Task]:
        """Triage everything waiting. Returns the tasks created or updated."""
        touched: list[Task] = []
        for event in await self._db.untriaged_mentions(self._batch_size):
            outcome = await self._decide(event)
            task = await self._apply(event, outcome)
            await self._db.mark_triaged(
                event, task.id if task else None, decision=_record(outcome)
            )
            if task is not None:
                touched.append(task)
        return touched

    async def _decide(self, event: InboundEvent) -> TriageOutcome:
        """Decide, and keep the call that decided it.

        Triage writes nothing, so this is where a call becomes a row — right
        beside the decision it produced, keyed on the same message.
        """
        context = await self._db.relevant_messages(event.conversation)
        calls: list = []
        outcome = await self._triage.decide(event, context=context, calls=calls)
        for call in calls:
            await self._db.record_model_call(
                message_id=event.provider_message_id,
                agent=call.agent,
                model=call.model,
                system_prompt=call.system_prompt,
                prompt=call.prompt,
                output=call.output,
                input_tokens=call.input_tokens,
                output_tokens=call.output_tokens,
                created_at=call.created_at,
            )
        return outcome

    async def _apply(
        self, event: InboundEvent, outcome: TriageOutcome
    ) -> Task | None:
        if isinstance(outcome, NeedsHuman):
            return await self._open(
                event, "unknown", 0.0, {"reason": outcome.reason}, NEEDS_HUMAN
            )

        if outcome.type == "skip":
            log.debug("skipped %s", event.provider_message_id)
            return None

        # Did they answer something we asked? A reply names the message it
        # responds to, and if we sent that message we already know which task
        # it was about. That answer is looked up, not classified — the
        # classifier does not agree with itself between runs, and "what type is
        # this message" is the wrong question to ask of an answer to our own
        # question anyway.
        answered = await self._db.task_answered_by(event.reply_to)
        if answered is not None:
            return await self._follow_up(answered, outcome, answers_us=True)

        existing = await self._db.open_task_for(event.conversation)
        if existing is not None:
            followed = await self._follow_up(existing, outcome)
            if followed is not None:
                return followed
            # A different subject. Fall through and open its own task — the
            # old one has been flagged for a human, and absorbing this into it
            # would be losing a report to say something about another one.

        state = PENDING if outcome.confidence >= self._threshold else NEEDS_HUMAN
        if state == NEEDS_HUMAN:
            log.info(
                "low confidence (%.2f) on %s — asking a human",
                outcome.confidence,
                event.provider_message_id,
            )
        return await self._open(event, outcome.type, outcome.confidence, {}, state)

    async def _follow_up(
        self, task: Task, outcome: Decided, *, answers_us: bool = False
    ) -> Task | None:
        """A later message in a conversation already being worked on.

        Two things can arrive in a follow-up. A change of subject — a bug report
        that has turned into something else — must not relabel the task without
        someone noticing. Otherwise it is more detail about the same problem,
        and the answer to a question we asked comes back as an ordinary message:
        if it does not reach the task, the task waits forever for something it
        has already been told.

        This used to merge parameters lifted from the follow-up, which was the
        second place triage extracted. It does not now. The message is linked
        to the task, the task goes back to pending, and the extractor reads
        everything the reporter has said — including the answer.

        Returns `None` when the message turns out not to belong to this task at
        all, which is the caller's cue to open one for it.
        """
        if answers_us:
            # They replied to a question we asked, so this belongs to that task
            # whatever it looks like. The type check below is for an unprompted
            # message in a conversation with work in flight — a report that has
            # drifted into something else. An answer has not drifted; it may
            # not even be an answer ("correlationId là cái gì a nhỉ"), and that
            # is still about this task.
            log.info(
                "task %d: a reply to something we asked (triage said %s)",
                task.id,
                outcome.type,
            )
        elif outcome.type != task.type:
            # Two things are true and only one used to be acted on. The task in
            # flight has changed subject and a person should look — *and* this
            # message is a report of its own, which used to be absorbed: it was
            # marked triaged against the old task and no task was ever opened
            # for it. A mention that produces no task is a dropped mention.
            #
            # `None` means "not a follow-up after all"; the caller opens one.
            log.info(
                "task %d was %s, this looks like %s — asking a human, and "
                "opening a task for the new one",
                task.id,
                task.type,
                outcome.type,
            )
            await self._db.move_task(task.id, NEEDS_HUMAN)
            return None

        # Back to pending, whatever it said. Whether the follow-up supplied
        # anything is not a question this can answer any more — the message has
        # to be *read* for that, and reading it is the extractor's job.
        # Re-planning is what gets it read; the workflow bounds how often the
        # reporter is asked the same thing.
        log.info("task %d: follow-up — re-planning", task.id)
        if task.state == ASKED:
            await self._db.move_task(task.id, PENDING)
            return replace(task, state=PENDING)
        return task

    async def _open(
        self,
        event: InboundEvent,
        type_: str,
        confidence: float,
        params: dict,
        state: str,
    ) -> Task:
        task = await self._db.create_task(
            conversation=event.conversation,
            type=type_,
            state=state,
            confidence=confidence,
            params=params,
        )
        log.info(
            "task %d opened (%s, %s) from %s",
            task.id,
            type_,
            state,
            event.provider_message_id,
        )
        return task
