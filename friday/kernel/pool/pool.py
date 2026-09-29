"""The pool: pulls pending tasks and hosts their graphs.

Four things, every pass — nothing else: stand down for a task the operator
answered themselves, announce to the operator whatever nobody can act on, host
the graph for whatever tasks are pending, and turn what came back into rows.
Deciding *what* to do with a task is the graph's business (`friday/kernel/dag/`); this
owns *when* and *whether what came back may be sent*, and does not import the
graph engine's vocabulary — `Ask`, `Reply`, `HandOver` come from the domain,
which both sides read, so the two no longer import each other.

Was `friday/workflows/runner.py`'s `WorkflowRunner`, renamed and moved here in
ticket 09 once every task type ran through the graph engine (tickets 01–08)
and there was no second thing left in `friday/workflows/` for this to share a
package with.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from typing import Any

from friday.kernel import outbox_card as card
from friday.kernel.dag import adapter, registry
from friday.kernel.dag.router import dag_for
from friday.kernel.domain.state import FridayState
from friday.kernel.domain.states import TaskState
from friday.kernel.domain.tasks import Task
from friday.kernel.ops.redact import scrub
from friday.kernel.outbox import DEFAULT_APPROVER, DEFAULT_SENDER, Kind
from friday.kernel.responder.check import rejected
from friday.sdk.actions import Ask, HandOver, Outcome, Reply
from friday.sdk.workflow import DAGState, NodeRun, status_of
from friday.sdk.workflow import Deps as DAGDeps
from friday.store.db import Database

__all__ = ["ASKED", "NEEDS_HUMAN", "PENDING", "REVIEW", "Pool"]

log = logging.getLogger(__name__)

PENDING = TaskState.PENDING
ASKED = TaskState.WAITING_FOR_DETAILS
NEEDS_HUMAN = TaskState.NEEDS_HUMAN
REVIEW = TaskState.REVIEW
HANDLED = TaskState.HANDLED_BY_OPERATOR


class Pool:
    """Turns a pending task into an action.

    Produces outbound intents; it never delivers one. Deciding what to say and
    knowing where to put it are different jobs, and the Outbox is the only
    module that talks to a provider.

    Only one message is allowed out without review: the request for missing
    details. It completes the task's own required parameters rather than
    speaking for the operator, and being wrong about it costs the reporter one
    unnecessary question.
    """

    @classmethod
    def build(cls, config, *, db: Database, responder=None) -> Pool:
        """The pool, built from the `workflows:` block.

        Nothing about an agent reaches here: an agent is a node inside a graph,
        and `register_dags` built those. This decides *when* a task is worked
        and *whether* what came back may be sent, and neither is a question a
        model answers.
        """
        return cls(
            db=db,
            responder=responder,
            auto_ask=config.workflows.auto_ask_for_details,
            max_asks=config.workflows.max_asks,
            concurrency=config.workflows.concurrency,
        )

    def __init__(
        self,
        *,
        db: Database,
        auto_ask: bool,
        responder=None,
        max_asks: int = 3,
        sender: str = DEFAULT_SENDER,
        #: Which identity asks. Not the one that speaks: buttons are an
        #: application-only feature, so the question goes out as the bot.
        approver: str = DEFAULT_APPROVER,
        batch_size: int = 20,
        concurrency: int = 2,
    ) -> None:
        self._db = db
        self._auto_ask = auto_ask
        self._responder = responder
        # Asked of the responder rather than passed in beside it. It is the
        # responder's knob; this loop only fetches what it is told to fetch.
        self._tone_examples = getattr(responder, "tone_examples", 8)
        self._max_asks = max_asks
        self._sender = sender
        self._approver = approver
        self._batch_size = batch_size
        self._slots = asyncio.Semaphore(concurrency)
        #: Tasks a pass has taken and not yet finished. A task stays `pending`
        #: for as long as its graph runs, so without this a second pass that
        #: starts meanwhile would read it as waiting and act on it again.
        self._taken: set[int] = set()

    async def run_forever(self, poll_interval_seconds: float = 2.0) -> None:
        while True:
            if not await self.run_once():
                await asyncio.sleep(poll_interval_seconds)

    async def run_once(self) -> list[Task]:
        """One pass. Pending tasks are worked side by side, at most
        `concurrency` at a time, so one slow graph does not hold every other
        task in the batch behind it.

        Still one pass: it returns when everything it took has finished, and
        standing down and raising hands stay before and after the batch
        exactly as they were.
        """
        await self._stand_down()
        pending = await self._db.tasks_in_state(PENDING, self._batch_size)
        mine = [task for task in pending if task.id not in self._taken]
        self._taken.update(task.id for task in mine)
        acted = list(await asyncio.gather(*(self._take(task) for task in mine)))
        await self._raise_hands()
        return acted

    async def _take(self, task: Task) -> Task:
        try:
            async with self._slots:
                return await self._act(task)
        finally:
            self._taken.discard(task.id)

    async def _stand_down(self) -> None:
        """The operator answered it themselves. Stop.

        Before anything else in the pass, so a task they have just handled is
        neither asked about nor drafted for. Whatever was queued about it is
        withdrawn too — a `reply` waits for approval with no expiry, so without
        this, approving it two days later sends an answer that stopped being
        true the moment they typed.

        Silent. A message saying "I cancelled something that should not have
        gone out" is noise about a thing that correctly did not happen.
        """
        for task in await self._db.tasks_the_operator_handled():
            withdrawn = await self._db.cancel_outbound_for(task.id)
            # A workflow suspended waiting on the reporter must not sit there:
            # the operator has taken the task off the board.
            await adapter.cancel(_wfid(task.id))
            await self._db.move_task(task.id, HANDLED)
            log.info(
                "task %d: the operator answered it — closing%s",
                task.id,
                f", {withdrawn} queued message(s) withdrawn" if withdrawn else "",
            )

    async def _raise_hands(self) -> None:
        """Tell the operator about work nobody can act on.

        A task in a column nobody is watching is the same as a lost one. Told
        once *per thing there is to say* — a notification that repeats is one
        you learn to ignore, but a second, different question is not a repeat.

        "Told once, ever" was right while the message was the task's type and
        its parameters, which do not change while it sits there. It is not any
        more: a graph pauses with its own question, and when the reporter
        answers, the state is discarded, the graph re-runs, and it can pause
        on a different one. Keyed on the fact of a row rather than on its
        text, the second question would be swallowed by the first answer.

        Three queries whatever the batch size, because this runs every two
        seconds and usually has nothing to do.
        """
        tasks = await self._db.tasks_in_state(NEEDS_HUMAN, self._batch_size)
        if not tasks:
            return
        pauses = await self._db.pauses_for([task.id for task in tasks])
        said = await self._db.announced(
            Kind.HELP_WANTED, state=NEEDS_HUMAN, limit=self._batch_size
        )

        for task in tasks:
            already = said.get(task.id, ())
            if len(already) >= self._max_asks:
                # A bound, not a rule. Told-once-per-thing-to-say is the rule,
                # and it holds as long as the thing to say is stable. It was
                # not: a reworded parameter made a new text, and the operator
                # got nineteen direct messages about one task. Whatever makes
                # the text move next, it stops here.
                log.debug(
                    "task %d: %d announcements already, saying no more",
                    task.id,
                    len(already),
                )
                continue
            # A graph that stopped to ask something asked a *specific*
            # question. Announcing only the task's type and parameters sends
            # the operator to the board to find out what was actually wanted,
            # which is the one thing this message exists to save them.
            # ponytail: one query per needs_human task per pass. Few of them
            # by construction; batch if the column ever fills up.
            text = _stuck(
                task, pauses.get(task.id), await self._db.last_said_by_reporter(task.id)
            )
            if text in already:
                continue
            await self._db.queue_outbound(
                task_id=task.id,
                conversation=task.conversation,
                kind=Kind.HELP_WANTED,
                sender=self._approver,
                text=text,
            )
            log.info("task %d: asked the operator to look", task.id)

    async def _act(self, task: Task) -> Task:
        return await self._route(task, await self._plan(task))

    async def _route(self, task: Task, action: Outcome) -> Task:
        """Given a decision, do what it says.

        One branch per kind, and none of them falls through to another. Each
        answers the same four questions — where the text came from, whether
        anything has to reword it, which rows it becomes, and where the task
        goes — and they only differ in the answers:

            Ask       the reporter    reworded    1 row     -> asked
            Reply     the reporter    as written  2 rows    -> review
            HandOver  the operator    as written  0 rows    -> needs_human

        `Reply` is the one that waits for approval, and it is *not* the one
        addressed to the operator: it answers the reporter in their name,
        which is exactly why somebody has to say yes first. The operator's own
        two rows are the approval card `_propose` queues beside it, and the
        help-wanted `_raise_hands` sends about a hand-over.
        """
        if isinstance(action, Reply):
            return await self._propose(task, action.text)
        if isinstance(action, Ask):
            return await self._ask(task, action)
        return await self._hand_over(task, action)

    async def _ask(self, task: Task, ask: Ask) -> Task:
        """Put the task's own missing details to the reporter.

        Two guards first, both about whether to ask *at all*, before anything
        is worded: the bound on how often one task may ask, and whether asking
        is switched on. Failing either is a person's problem, not a reason to
        stay quiet — an unanswered question that stops being asked has to
        surface somewhere.
        """
        asked = await self._db.outbound_count(task.id, kind=Kind.ASK_FOR_DETAILS)
        if asked >= self._max_asks:
            log.info(
                "task %d: asked %d times without an answer — a human's now",
                task.id,
                asked,
            )
            return await self._escalate(task, ask.text)
        if not self._auto_ask:
            log.info("task %d: would ask, but auto_ask is off", task.id)
            return await self._escalate(task, ask.text)

        text = await self._in_the_operators_voice(task, ask.text)
        await self._db.queue_outbound(
            task_id=task.id,
            conversation=task.conversation,
            kind=Kind.ASK_FOR_DETAILS,
            sender=self._sender,
            text=text,
            reply_to=await self._db.last_mention_in(task.conversation),
        )
        log.info("task %d: asked — %r", task.id, text)
        return await self._move(task, ASKED)

    async def _hand_over(self, task: Task, hand_over: HandOver) -> Task:
        """Nothing here can take it further. The specific reason is stored so
        `_raise_hands` can carry it later in the same pass rather than the
        task's bare type; no row is queued at this point."""
        log.info("task %d: %s", task.id, hand_over.reason)
        await self._db.set_pause(task.id, hand_over.reason)
        return await self._move(task, NEEDS_HUMAN)

    async def _escalate(self, task: Task, reason: str) -> Task:
        """Asking is exhausted or switched off, so a person takes it. The
        workflow was suspended waiting on the reporter — cancel it, store the
        reason for `_raise_hands`, and move the task to a human."""
        await adapter.cancel(_wfid(task.id))
        await self._db.set_pause(task.id, reason)
        return await self._move(task, NEEDS_HUMAN)

    async def _propose(self, task: Task, text: str) -> Task:
        """An answer, and the question that asks whether to send it.

        Both are rows, so a card nobody could deliver shows up rather than
        leaving an answer waiting for a decision nobody was asked for. The card
        names the reply it asks about: approval belongs to that row, not to
        the task, so a reply queued later waits for its own card.

        **Redaction runs on the draft** (§12): the reply is `scrub`bed before it
        is queued, so a secret in a drafted answer never leaves even if the
        operator approves it. The card then shows those exact scrubbed bytes and
        flags that a redaction happened — the operator approves what will go out,
        with the truth about it (`outbox.card`).
        """
        redacted = scrub(text)
        reply = await self._db.queue_outbound(
            task_id=task.id,
            conversation=task.conversation,
            kind=Kind.REPLY,
            sender=self._sender,
            text=redacted,
            reply_to=await self._db.last_mention_in(task.conversation),
        )
        await self._db.queue_outbound(
            task_id=task.id,
            conversation=task.conversation,
            kind=Kind.APPROVAL_CARD,
            sender=self._approver,
            text=card.render(text, destination=task.conversation, reply_id=reply.id),
            approves=reply.id,
        )
        log.info("task %d: proposed an answer — %r", task.id, redacted)
        # Waiting on the operator, not on the reporter. Different people,
        # different columns, different thing to chase.
        return await self._move(task, REVIEW)

    async def _in_the_operators_voice(self, task: Task, template: str) -> str:
        """The template, or the same thing in the operator's voice.

        **Only `Ask` comes through here, and that is not an inconsistency.**
        A `Reply` arrives already written in the operator's voice by whatever
        produced it — nothing does today, since the graph node that did went
        with the five-node `trace_problem`. An `Ask` was assembled
        by `_question()` — code, no model — so it has no voice until this
        gives it one. This brings asking up to where answering already starts;
        it does not treat the two differently.

        Asking is the agent's own decision, whoever phrased it: the risk in
        this system is in *answering*, not in asking, and a request for a
        correlationId is harmless however it is worded. The operator is
        interrupted for answers and for trouble.

        A responder that cannot write it falls back to the template rather than
        producing nothing: a wrong message in someone's name is worse than a
        plain one, and silence is worse than both.

        **And so does a responder that wrote the wrong thing.** This is the
        only message the system sends without a person reading it first, and
        the sentence that justified that — what is being asked never changes,
        only the wording does — was enforced by nothing at all. It is enforced
        by `friday.kernel.responder.check` now: the draft has to still name what the
        template named, carry no link or code, stay near its length, and
        promise nothing. Code is the floor, the same rule `prepare` states for
        validation.
        """
        if self._responder is None:
            return template
        draft = await self._responder.draft(
            asking=template,
            params=_as_params(task),
            # What this run is about, as one value. `about_message` carries
            # the message that opened this task, so a memory written while
            # the responder is drafting reaches the row through
            # `FridayState.message_id` and the Rooms screen joins its
            # enrichment glyph on it. Without that, every enrichment marker
            # on the screen is wrong.
            state=FridayState.for_conversation(task.conversation, agent="responder")
            .for_task(task.id)
            .about_message(await self._db.source_message_of(task.id)),
            stranger=await self._stranger(task),
            context=await self._db.relevant_messages(task.conversation),
            tone=await self._db.tone_examples(limit=self._tone_examples),
        )
        if draft is None:
            return template
        if (reason := rejected(draft.text, asking=template)) is not None:
            # Logged, because a fallback nobody sees hides a prompt
            # regression — and somebody thought the wording was worth a model
            # call, so it is worth a line when it is thrown away.
            log.info("task %d: the drafted question was not sent — %s", task.id, reason)
            return template
        return draft.text

    async def _stranger(self, task: Task) -> bool:
        """Nobody the operator has written to before, and nobody they have
        written down. Learning a voice from history fails exactly here — no
        history, so the average, and the average is how they write to their
        own team — so the default has to be the safer register, not the
        average one."""
        who = await self._db.reporter_of(task.id)
        if who is None:
            return False
        author_id, _ = who
        # Written down is a `person` row keyed on their Discord id, here or
        # for every room. It was a name in a channel file's `people:` map,
        # asked of the responder; the files are gone (board
        # `read-it-the-way-the-operator-does`, ticket 10) and a row is the
        # store's to answer.
        named = await self._db.knows_person(task.conversation.channel_id, author_id)
        return not named and not await self._db.has_exchanged_with(author_id)

    async def _plan(self, task: Task) -> Outcome:
        """Route to this type's graph. Every classifiable type has one — see
        `register_dags` — so there is no second way to decide what to do with
        a task any more."""
        params_type = registry.decision_params().get(task.type)
        if params_type is None:
            return HandOver(f"unknown task type {task.type!r}")

        try:
            params_type(**task.params)
        except TypeError as exc:
            # Stored parameters that no longer fit their type — a schema change
            # landing on rows written before it. Work, not a crash.
            return HandOver(f"cannot read {task.type} parameters: {exc}")

        dag = dag_for(task.type)
        assert dag is not None, f"{task.type!r} is registered but has no graph"
        return await self._run_dag(dag, task)

    async def _run_dag(self, dag, task: Task) -> Outcome:
        """Work this task's graph on DBOS (ticket 06).

        Node 0 — `dag.entry`, `prepare` for every graph — is run here, once,
        every pass, outside the durable workflow: there is a new reporter
        message since the last pass, and node 0 is the extractor that reads it.
        If it decides the answer — an `Ask`/`Reply`/`HandOver`, which a one-node
        graph always does — that is the outcome and no workflow runs.

        Past that (`trace_problem`'s investigation) the graph runs as a durable
        DBOS workflow, `prepare` pre-seeded so the walk starts at `resolve`.
        The workflow persists across passes: a node that must ask the reporter
        suspends it, and a later pass — after the reporter's answer re-planned
        the task — resumes it in place with the re-extracted parameters, rather
        than re-running the whole graph.
        """
        deps = self._deps_for(task)
        prepared = await self._run_entry(dag, deps, task)
        if status_of(prepared) in _FAILED:
            log.warning(
                "task %d: %s's %s failed — %s",
                task.id,
                dag.name,
                dag.entry,
                prepared["reason"],
            )
            return HandOver(f"{dag.name} failed: {prepared['reason']}")
        if isinstance(prepared, (Ask, Reply, HandOver)):
            return prepared
        return await self._run_workflow(dag, task, prepared)

    async def _run_entry(self, dag, deps: DAGDeps, task: Task) -> Any:
        """Run node 0 outside the workflow, through the adapter's shared invoke —
        the same clock, retry, redaction and `node_runs` record every node in
        the workflow gets. An exception comes back as `{status: error}`, not a
        crash."""
        return await adapter.run_node(
            dag.name, dag.node(dag.entry), DAGState.empty(), deps, self._recorder(task)
        )

    async def _run_workflow(self, dag, task: Task, prepared: Any) -> Outcome:
        """Start or resume this task's durable workflow, then poll it to its
        next boundary — the action it decided, or the question it waits on."""
        wfid = _wfid(task.id)
        st = await adapter.status(wfid)
        if st is None:
            await adapter.start(
                dag.name,
                self._scope(task, seed={dag.entry: prepared}),
                workflow_id=wfid,
            )
        else:
            waiting = await adapter.pending(wfid)
            if waiting is not None:
                # A reporter reply re-planned the task; hand the freshly
                # extracted parameters to the node that was waiting, and it
                # searches again with what it now knows.
                await adapter.answer(wfid, waiting["node"], prepared)
        return await self._poll(dag, wfid)

    async def _poll(self, dag, wfid: str) -> Outcome:
        """Drive the workflow to a boundary: the outcome it reached, or the
        `Ask` it suspended on. The graph runs fast between suspensions, so this
        never blocks on a human — it returns the moment the run waits."""
        while True:
            st = await adapter.status(wfid)
            if st == "done":
                return self._outcome(dag, await adapter.result(wfid))
            if st == "failed":
                return HandOver(f"{dag.name} did not finish")
            waiting = await adapter.pending(wfid)
            if waiting is not None:
                return Ask(waiting["text"])
            await asyncio.sleep(0.02)

    def _scope(self, task: Task, *, seed: dict) -> dict:
        """A workflow's serializable input: the task to rebuild `Deps` from, and
        node 0's result to pre-seed so the walk starts past it."""
        return {"task_id": task.id, "task_type": task.type, "_seed": seed}

    def _deps_for(self, task: Task) -> DAGDeps:
        """What node 0 is handed. The workflow's own nodes get theirs rebuilt
        inside the run by the adapter's deps factory; this is node 0's alone —
        built the same way: the base `Deps`, enriched by the task type's own
        deps factory (ticket 13) when it has one."""
        base = DAGDeps(task=task, db=self._db, servers=dict(registry.SERVERS))
        enrich = registry.deps_of(task.type)
        return enrich(base) if enrich is not None else base

    def _recorder(self, task: Task):
        """Where a graph's attempts are written: `node_runs`, under this task."""

        async def record(run: NodeRun) -> None:
            await self._db.record_node_run(task_id=task.id, **asdict(run))

        return record

    @staticmethod
    def _outcome(dag, results: dict) -> Outcome:
        """What the graph decided, off the results the workflow returned.

        A graph that finished without producing an `Outcome` has not said what
        to send, and handing over is the honest answer. The results mapping is
        in walk order, so the deciding node is the last `Outcome` in it.
        """
        final = DAGState(results=results)
        node = _deciding_node(final, list(results.keys()))
        if node is not None:
            result = final[node]
            if status_of(result) in _FAILED:
                return HandOver(f"{dag.name} failed at {node}: {result['reason']}")
            return result
        return HandOver(f"{dag.name} finished without deciding what to send")

    async def _move(self, task: Task, state: str) -> Task:
        await self._db.move_task(task.id, state)
        from dataclasses import replace

        return replace(task, state=state)


def _as_params(task: Task):
    """The task's parameters as their declared type, or None if they no longer
    fit it. Best effort: the responder is better off with no params than with
    a crash, and `_plan` has already handed over anything malformed."""
    params_type = registry.decision_params().get(task.type)
    if params_type is None:
        return None
    try:
        return params_type(**task.params)
    except TypeError:
        return None


def _wfid(task_id: int) -> str:
    """One durable workflow per task, keyed by its id, so a pass finds the run
    an earlier pass started (suspended on the reporter, or still going)."""
    return f"task-{task_id}"


#: The envelope statuses the runner writes when a node did not finish: its
#: clock ran out, or it raised. Neither is a completion to resume past.
_FAILED = frozenset({"timed_out", "error"})


def _deciding_node(final: DAGState, trail: list[str]) -> str | None:
    """Which node in the trail produced the graph's `Outcome` — or, failing
    that, the failure it ended on (`timed_out`, `error`), which is the honest
    answer where an exception used to end the run and carry its reason. Reading
    backwards along the path the run actually took rather than the order the
    nodes were declared in. A graph often ends with bookkeeping — an audit
    line, a cleanup — declared after the node that decides, and letting
    declaration order answer means that bookkeeping silently discards the
    reply. `None` if no node on the trail produced one at all.
    """
    for name in reversed(trail):
        if final.has(name) and isinstance(final[name], (Ask, Reply, HandOver)):
            return name
    for name in reversed(trail):
        if final.has(name) and status_of(final[name]) in _FAILED:
            return name
    return None


def _stuck(
    task: Task,
    reason: str | None = None,
    last_said: str | None = None,
) -> str:
    """What it is, what it knows, and what the reporter last said — enough to
    judge, and to answer, without opening anything.

    The last message matters most when the task stopped *because of it*: a
    question the agent could not answer. Without it the operator sees a task
    with a correlationId in it and no hint that a person is waiting on a
    sentence they could type in five seconds. Quoted, so it reads as theirs.
    """
    known = ", ".join(f"{k}: {v}" for k, v in sorted(task.params.items()) if v)
    line = f"{task.type} #{task.id} — {known or 'nothing extracted'}"
    if reason:
        line += f"\n{reason}"
    if last_said:
        quoted = last_said.strip().replace("\n", "\n> ")
        line += f"\nthey last said:\n> {quoted}"
    return line
