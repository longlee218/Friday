"""The pool: claims pending tasks and starts their passes.

Claim, concurrency, start or recover a pass, stand down for a task the
operator answered themselves, and raise hands for what nobody can act on —
nothing else (build-the-spine ticket 14; board `domains-plug-in` ticket 14
§2). What a task comes to is the spine pass's (`friday/kernel/spine/`),
routing included: its last step, `deliver`, queues the rows and moves the
task. The actions the spine does not run yet (`backend.answer_question`,
`ops.request_permission`, until tickets 15–16) still run their DAG's node 0
here, and what it decides goes through the same `deliver`. Over 200 lines
until ticket 16 deletes that DAG route (`_plan`, `_deps_for`, `_recorder`).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, replace
from typing import Any

from friday.kernel.dag import adapter, registry
from friday.kernel.dag.router import dag_for
from friday.kernel.domain.states import TaskState
from friday.kernel.domain.tasks import Task
from friday.kernel.outbox import DEFAULT_APPROVER, DEFAULT_SENDER, Kind
from friday.kernel.spine.deliver import deliver
from friday.kernel.spine.workflow import pass_id
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
    """Turns a pending task into a pass.

    Produces outbound intents through `deliver`; it never delivers one. The
    Outbox is the only module that talks to a provider.
    """

    @classmethod
    def build(cls, config, *, db: Database, spine: Any = None) -> Pool:
        """The pool, built from the `workflows:` block. `spine` is the bound
        `Spine` the composition root built; `None` runs every task on its
        DAG's node 0 (tests)."""
        return cls(
            db=db,
            spine=spine,
            auto_ask=config.workflows.auto_ask_for_details,
            max_asks=config.workflows.max_asks,
            concurrency=config.workflows.concurrency,
        )

    def __init__(
        self,
        *,
        db: Database,
        auto_ask: bool,
        spine: Any = None,
        #: How many different help-wanted messages one task may raise.
        max_asks: int = 3,
        sender: str = DEFAULT_SENDER,
        #: Which identity asks the operator. Not the one that speaks: buttons
        #: are an application-only feature, so the card goes out as the bot.
        approver: str = DEFAULT_APPROVER,
        batch_size: int = 20,
        concurrency: int = 2,
    ) -> None:
        self._db = db
        self._spine = spine
        self._auto_ask = auto_ask
        self._max_asks = max_asks
        self._sender = sender
        self._approver = approver
        self._batch_size = batch_size
        self._slots = asyncio.Semaphore(concurrency)
        #: Tasks a pass has taken and not yet finished. A task stays `pending`
        #: for as long as its pass runs, so without this a second pool pass
        #: that starts meanwhile would read it as waiting and act on it again.
        self._taken: set[int] = set()

    async def run_forever(self, poll_interval_seconds: float = 2.0) -> None:
        while True:
            if not await self.run_once():
                await asyncio.sleep(poll_interval_seconds)

    async def run_once(self) -> list[Task]:
        """One pool pass. Pending tasks are worked side by side, at most
        `concurrency` at a time, so one slow pass does not hold every other
        task in the batch behind it. Standing down and raising hands stay
        before and after the batch."""
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
            # A pass still running must not deliver over their answer.
            await adapter.cancel(pass_id(task.id, task.pass_no))
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
        """Start (or, after a restart, join) the task's pass on the spine;
        an action the spine does not run yet runs its DAG's node 0, and what
        that decides is delivered the same way."""
        if self._spine is not None and self._spine.runs(task.type):
            wfid = pass_id(task.id, task.pass_no)
            try:
                state = await adapter.run_pass(task.id, task.pass_no, wfid)
            except Exception as exc:  # noqa: BLE001 — a failed pass is work
                # DBOS keeps the pass failed, so joining it again would fail
                # the same way every pool pass: a person takes it instead.
                log.warning("task %d: pass %s failed — %s", task.id, wfid, exc)
                state = await self._deliver(
                    task, HandOver(f"pass_failed: {wfid}: {type(exc).__name__}")
                )
            return replace(task, state=state)
        heard = await self._db.last_reporter_turn_at(task.id)
        return replace(
            task, state=await self._deliver(task, await self._plan(task), heard)
        )

    async def _deliver(self, task: Task, outcome: Outcome, heard=None) -> str:
        return await deliver(
            self._db,
            task,
            task.pass_no,
            outcome,
            heard_until=heard,
            asks_since=None,
            sender=self._sender,
            approver=self._approver,
            auto_ask=self._auto_ask,
        )

    async def _plan(self, task: Task) -> Outcome:
        """This type's DAG node 0 — `prepare` for every type still on the DAG
        path, which always decides: an `Ask`, a `Reply` or a `HandOver`."""
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
        prepared = await adapter.run_node(
            dag.name,
            dag.node(dag.entry),
            DAGState.empty(),
            self._deps_for(task),
            self._recorder(task),
        )
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
        # Past node 0 was `trace_problem`'s investigation, which runs on the
        # spine now (ticket 14); nothing here runs a graph past it.
        return HandOver(f"{dag.name} runs on the spine, which is not configured")

    def _deps_for(self, task: Task) -> DAGDeps:
        """What node 0 is handed: the base `Deps`, enriched by the task
        type's own deps factory (ticket 13) when it has one."""
        base = DAGDeps(task=task, db=self._db, servers=dict(registry.SERVERS))
        enrich = registry.deps_of(task.type)
        return enrich(base) if enrich is not None else base

    def _recorder(self, task: Task):
        """Where node 0's attempts are written: `node_runs`, under this task."""

        async def record(run: NodeRun) -> None:
            await self._db.record_node_run(task_id=task.id, **asdict(run))

        return record


#: The envelope statuses a node that did not finish returns: it raised, or
#: its clock ran out. Neither is a decision.
_FAILED = frozenset({"timed_out", "error"})


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
