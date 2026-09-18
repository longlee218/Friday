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
from datetime import datetime, timezone

from friday.config import message_age_cutoff
from friday.store.db import Database
from friday.domain.states import TaskState
from friday.domain.models import SKIP, InboundEvent, Task
from friday.triage import Decided, NeedsHuman, Triage, TriageOutcome
from friday.triage.prefilter import Sensitive

__all__ = ["PENDING", "NEEDS_HUMAN", "TriageRunner", "build_triage"]

log = logging.getLogger(__name__)

#: What triage concluded about a message too old to be worth answering. A
#: decision and not a `TaskState` (D3): everything `classify` names opens
#: work, and this names the absence of it — the shape `skip` already has.
#: Deliberately not in `models.DECISIONS`, which decides what may become a
#: few-shot example: "this was old" is a fact about the clock, not something
#: to learn to predict from a message's text.
OUTDATED = "outdated"

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


async def build_triage(
    config, *, db: Database, record=None, spent=None
) -> Triage:
    """The real classifier, assembled the one place this is done.

    Split out of `TriageRunner.build` for ticket 06's eval harness:
    `evals/run_triage_eval.py` needs the same examples and the same
    sensitive-word prefilter production uses, and a second copy of "which
    examples, which model" is exactly the kind of duplicate this codebase
    keeps finding and keeps regretting after it has already drifted (ticket
    09's review caught `MemoryScope` behind a quoted forward reference and a
    bare `8` standing in for `RESULTS`). One function, two callers, instead.
    """
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

    return Triage(
        config=settings,
        examples=examples,
        sensitive=sensitive,
        record=record,
        spent=spent,
        # The room's summary row is read from here per call — the eval gets
        # the same reading production does, from whatever store it is given.
        summaries=db,
    )


class TriageRunner:
    @classmethod
    async def build(
        cls,
        config,
        *,
        db: Database,
        still_typing=None,
        record=None,
        spent=None,
    ) -> "TriageRunner":
        """Everything triage needs, read from configuration here.

        The composition root asks for a triage runner; it does not know that
        triage has a confidence threshold, or that it shows the classifier
        examples, or how many. Those are this step's knobs and this is where
        they are read — the same shape `register_extractors` and
        `register_dags` already use, for the same reason: adding a knob is a
        change here, not there.
        """
        try:
            settings = config.agents["triage"]
        except KeyError:
            raise SystemExit(
                "No 'triage' agent in config.yaml — see the agents section."
            ) from None

        return cls(
            db=db,
            triage=await build_triage(
                config, db=db, record=record, spent=spent
            ),
            confidence_threshold=float(
                settings.options.get("confidence_threshold", 0.7)
            ),
            max_message_age=message_age_cutoff(config),
            turn_seconds=config.ingest.turn_seconds,
            still_typing=still_typing,
        )

    def __init__(
        self,
        *,
        db: Database,
        triage,
        confidence_threshold: float,
        batch_size: int = 50,
        poll_interval_seconds: float = 2.0,
        #: How long the author has to be quiet before their turn is read.
        turn_seconds: float = 0.0,
        #: How old a turn may be before it is marked outdated rather than
        #: answered, in seconds. `None` is no cutoff, which is the shipped
        #: default and the behaviour that existed before this knob.
        max_message_age: float | None = None,
        #: `(conversation, author_id) -> bool`, from the inbox. Keeps a turn
        #: open past the window while they are still writing. Optional: the
        #: recovery sweep has no typing signal, and neither do tests.
        still_typing=None,
    ) -> None:
        self._db = db
        self._triage = triage
        self._threshold = confidence_threshold
        self._turn_seconds = turn_seconds
        self._max_age = max_message_age
        self._still_typing = still_typing or (lambda conversation, author_id: False)
        self._batch_size = batch_size
        self._poll_interval = poll_interval_seconds

    @property
    def confidence_threshold(self) -> float:
        """The line a classification is judged against.

        Exposed because the board draws a confidence *against* it and a
        number without its line is unreadable — and because the composition
        root must not read it out of `config.yaml` itself: which knobs a step
        has is that step's business, enforced by
        `test_no_agent_configuration_is_read_in_the_composition_root`, which
        is what caught this being done the other way.
        """
        return self._threshold

    async def run_forever(self) -> None:
        while True:
            if not await self.run_once():
                await asyncio.sleep(self._poll_interval)

    async def run_once(self) -> list[Task]:
        """Triage every finished turn. Returns the tasks created or updated.

        The queue holds mentions. A mention opens a *turn* — everything the
        same person went on to say — and it is the turn that is classified,
        once, when they have stopped. A mention whose turn is still open is
        left where it is and picked up next pass.
        """
        touched: list[Task] = []
        for event in await self._db.untriaged_mentions(self._batch_size):
            try:
                task = await self._triage_one(event)
            except Exception:  # noqa: BLE001 - one bad row is not the batch
                # Per message, because this runs inside the TaskGroup that
                # also holds ingest, the outbox, the board and the heartbeat.
                # An exception here took all of them down, and because it
                # escaped before `mark_triaged`, the row stayed at the head of
                # the queue and every restart died on it — a boot loop out of
                # one message. Left untriaged deliberately: it is still work
                # nobody has looked at, which is what the queue is for.
                log.exception(
                    "triage failed on %s — leaving it queued and going on",
                    event.provider_message_id,
                )
                continue
            if task is not None:
                touched.append(task)
        return touched

    async def _triage_one(self, event: InboundEvent) -> Task | None:
        """One message: read its turn, decide, apply, mark it read."""
        turn, closed_by_someone_else = await self._db.turn_from(event)
        if not turn:
            # `turn_from` shows only what it can attribute to the reporter, and
            # it excludes anything this process posted — so an empty turn means
            # the queued message is one of ours. Nothing to classify: mark it
            # read so it leaves the queue, and never call the model on an empty
            # string. The inbox now catches these on the way in (`we_sent`);
            # this is the floor under that, and what stops a row already in the
            # queue from wedging every restart.
            log.info(
                "%s is a message we posted — nothing to triage",
                event.provider_message_id,
            )
            await self._db.mark_triaged(event, None, decision={"type": "ours"})
            return None
        if not closed_by_someone_else and self._still_open(turn):
            return None
        stale = await self._too_old(event, turn)
        if stale is not None:
            await self._db.mark_triaged(
                event,
                None,
                decision={"type": OUTDATED, "confidence": 0.0, "params": {"reason": stale}},
            )
            log.info("%s is %s — not sent to the model", event.provider_message_id, stale)
            return None
        said = replace(event, text="\n".join(m.text for m in turn if m.text))
        outcome = await self._decide(said, turn=turn)
        task = await self._apply(event, outcome)
        await self._db.mark_triaged(
            event, task.id if task else None, decision=_record(outcome)
        )
        return task

    def _age(self, turn: list[InboundEvent]) -> float:
        """How old the turn is, in seconds, judged by its **newest** message.

        D5: three messages from yesterday and one from an hour ago are one
        turn, and it is fresh. Judging by the oldest would answer a reporter
        who came back to their own thread without the context they wrote —
        which is the failure this system has already shipped once, from the
        other direction.
        """
        newest = max(m.created_at for m in turn)
        return (datetime.now(timezone.utc) - newest).total_seconds()

    async def _too_old(
        self, event: InboundEvent, turn: list[InboundEvent]
    ) -> str | None:
        """Why this turn has stopped being worth answering, or `None`.

        A reason rather than a boolean, so the sentence is built where the
        cutoff is known to exist. The caller has no way to narrow
        `self._max_age` from a `True`, and formatting it there meant dividing
        an `Optional` by 3600.

        Never for a reply to something we asked (D6). A task in
        `WAITING_FOR_DETAILS` asked a question and is still waiting; the
        answer arriving three days late is still the answer, and the lookup
        that recognises it is the same one `_apply` uses rather than a second
        way of spotting the same thing.
        """
        cutoff = self._max_age
        if cutoff is None:
            return None
        if await self._db.task_answered_by(event.reply_to) is not None:
            return None
        age = self._age(turn)
        if age <= cutoff:
            return None
        return (
            f"written {age / 3600:.0f}h ago, older than the "
            f"{cutoff / 3600:.0f}h cutoff"
        )

    def _still_open(self, turn: list[InboundEvent]) -> bool:
        """They may not be finished: too recent, or still typing.

        Never called with an empty turn — `_triage_one` settles that case
        before this, because "the last thing they said" has no answer when
        they are not in the list at all.
        """
        last = turn[-1]
        quiet_for = (datetime.now(timezone.utc) - last.created_at).total_seconds()
        if quiet_for < self._turn_seconds:
            return True
        return self._still_typing(last.conversation, last.author_id)

    async def _decide(
        self, event: InboundEvent, *, turn: list[InboundEvent]
    ) -> TriageOutcome:
        """Decide.

        This used to drain a `calls` list and write each one to the store,
        which is why triage was the only agent whose prompts were ever kept:
        every other agent's `Harness.run` forgot the list. The harness holds
        the sink now (D1) and writes them itself, correlated by the
        `message_id` `Triage.decide` passes it.

        **This used to also fetch the unbounded relevance window here** —
        `context = await self._db.relevant_messages(event.conversation)` —
        every message that had ever mentioned the operator in this
        conversation, unbounded and growing forever (ticket 26). Ticket 09
        reverses that: `Triage` reads the room's own summary row from the
        store it holds, and `turn` — the raw messages `_triage_one` just
        read from `turn_from`, before they were joined into `event`'s own
        text — is what reaches the prompt instead of a transcript.
        """
        return await self._triage.decide(event, turn=turn)

    async def _apply(
        self, event: InboundEvent, outcome: TriageOutcome
    ) -> Task | None:
        if isinstance(outcome, NeedsHuman):
            return await self._open(
                event, "unknown", 0.0, {"reason": outcome.reason}, NEEDS_HUMAN
            )

        if outcome.type == SKIP:
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
