"""Running workflows over tasks that are ready for one."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from friday.dag import DAGDeps, DAGRunner, DAGState, PauseForHuman
from friday.dag.router import dag_for
from friday.db import Database
from friday.tasks import TaskState
from friday.models import Task
from friday.outbox import Kind
from friday.workflows import PARAMS, Action, Ask, Park, Reply, plan

__all__ = ["ASKED", "NEEDS_HUMAN", "PENDING", "REVIEW", "WorkflowRunner"]

log = logging.getLogger(__name__)

PENDING = TaskState.PENDING
ASKED = TaskState.WAITING_FOR_DETAILS
NEEDS_HUMAN = TaskState.NEEDS_HUMAN
REVIEW = TaskState.REVIEW


class WorkflowRunner:
    """Turns a pending task into an action.

    Produces outbound intents; it never delivers one. Deciding what to say and
    knowing where to put it are different jobs, and the Outbox is the only
    module that talks to a provider.

    Only one message is allowed out without review: the request for missing
    details. It completes the task's own required parameters rather than
    speaking for the operator, and being wrong about it costs the reporter one
    unnecessary question.
    """

    def __init__(
        self,
        *,
        db: Database,
        auto_ask: bool,
        responder=None,
        #: Handed to a planner that needs to look something up.
        agent=None,
        #: Overrides the registry — a step can be tried before it is registered.
        planners: dict | None = None,
        tone_examples: int = 8,
        max_asks: int = 3,
        #: How long to let a burst settle. Three messages in ten seconds
        #: each send the task back to be re-planned, and each would
        #: otherwise get its own reply.
        debounce_seconds: float = 45.0,
        sender: str = "discord_user",
        #: Which identity asks. Not the one that speaks: buttons are an
        #: application-only feature, so the question goes out as the bot.
        approver: str = "discord_bot",
        batch_size: int = 20,
    ) -> None:
        self._db = db
        self._auto_ask = auto_ask
        self._responder = responder
        self._agent = agent
        self._planners = planners
        self._tone_examples = tone_examples
        self._max_asks = max_asks
        self._debounce = debounce_seconds
        self._sender = sender
        self._approver = approver
        self._batch_size = batch_size

    async def run_forever(self, poll_interval_seconds: float = 2.0) -> None:
        while True:
            if not await self.run_once():
                await asyncio.sleep(poll_interval_seconds)

    async def run_once(self) -> list[Task]:
        acted: list[Task] = []
        for task in await self._db.tasks_in_state(PENDING, self._batch_size):
            acted.append(await self._act(task))
        await self._raise_hands()
        return acted

    async def _raise_hands(self) -> None:
        """Tell the operator about work nobody can act on.

        A task in a column nobody is watching is the same as a lost one. Told
        once, because a notification that repeats is one you learn to ignore —
        and the outbox row is itself the record of having told them, which is
        why finding the untold ones is one anti-join rather than a count per
        task on every poll.
        """
        waiting = await self._db.tasks_needing_announcement(
            Kind.HELP_WANTED, state=NEEDS_HUMAN, limit=self._batch_size
        )
        for task in waiting:
            await self._db.queue_outbound(
                task_id=task.id,
                conversation=task.conversation,
                kind=Kind.HELP_WANTED,
                sender=self._approver,
                text=_stuck(task),
            )
            log.info("task %d: asked the operator to look", task.id)

    async def _act(self, task: Task) -> Task:
        action = await self._plan(task)

        if isinstance(action, Reply):
            return await self._propose(task, action.text)

        if isinstance(action, Ask):
            if await self._too_soon(task):
                return task
            asked = await self._db.outbound_count(task.id, kind=Kind.ASK_FOR_DETAILS)
            if asked >= self._max_asks:
                log.info(
                    "task %d: asked %d times without an answer — a human's now",
                    task.id,
                    asked,
                )
                return await self._move(task, NEEDS_HUMAN)

        if isinstance(action, Ask) and self._auto_ask:
            text = await self._say(task, action.text)
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

        if isinstance(action, Ask):
            log.info("task %d: would ask, but auto_ask is off", task.id)
        else:
            log.info("task %d: %s", task.id, action.reason)
        return await self._move(task, NEEDS_HUMAN)

    async def _propose(self, task: Task, text: str) -> Task:
        """An answer, and the question that asks whether to send it.

        Both are rows, so a card nobody could deliver shows up rather than
        leaving an answer waiting for a decision nobody was asked for.
        """
        await self._db.queue_outbound(
            task_id=task.id,
            conversation=task.conversation,
            kind=Kind.REPLY,
            sender=self._sender,
            text=text,
            reply_to=await self._db.last_mention_in(task.conversation),
        )
        await self._db.queue_outbound(
            task_id=task.id,
            conversation=task.conversation,
            kind=Kind.APPROVAL_CARD,
            sender=self._approver,
            text=text,
        )
        log.info("task %d: proposed an answer — %r", task.id, text)
        # Waiting on the operator, not on the reporter. Different people,
        # different columns, different thing to chase.
        return await self._move(task, REVIEW)

    async def _too_soon(self, task: Task) -> bool:
        """Let a burst settle before answering it.

        A pause, not a mute: the task stays pending and is asked on the next
        pass once the burst has passed. Someone typing "vẫn lỗi", "alo", "?" in
        ten seconds is one person waiting, not three questions.
        """
        if not self._debounce:
            return False
        last = await self._db.last_outbound_at(task.id, kind=Kind.ASK_FOR_DETAILS)
        if last is None:
            return False
        quiet_for = (datetime.now(timezone.utc) - last).total_seconds()
        if quiet_for >= self._debounce:
            return False
        log.debug("task %d: still settling (%.0fs)", task.id, quiet_for)
        return True

    async def _say(self, task: Task, template: str) -> str:
        """The template, or the same thing in the operator's voice.

        Asking is the agent's own decision, whoever phrased it: the risk in
        this system is in *answering*, not in asking, and a request for a
        correlationId is harmless however it is worded. The operator is
        interrupted for answers and for trouble.

        A responder that cannot write it falls back to the template rather than
        producing nothing: a wrong message in someone's name is worse than a
        plain one, and silence is worse than both.
        """
        if self._responder is None:
            return template
        draft = await self._responder.draft(
            asking=template,
            context=await self._db.relevant_messages(task.conversation),
            tone=await self._db.tone_examples(limit=self._tone_examples),
        )
        return template if draft is None else draft.text

    async def _plan(self, task: Task) -> Action:
        params = PARAMS.get(task.type)
        if params is None:
            return Park(f"unknown task type {task.type!r}")

        dag = dag_for(task.type)
        if dag is not None:
            return await self._run_dag(dag, task)

        try:
            return await plan(
                task.type,
                params(**task.params),
                agent=self._agent,
                planners=self._planners,
                text=await self._db.original_text_for(task.id),
            )
        except TypeError as exc:
            # Stored parameters that no longer fit their type — a schema change
            # landing on rows written before it. Work, not a crash.
            return Park(f"cannot read {task.type} parameters: {exc}")

    async def _run_dag(self, dag, task: Task) -> Action:
        """Run the graph registered for this task type.

        The runner records each node before starting the next, so a crash
        mid-graph resumes here rather than starting over. A node that cannot
        decide raises `PauseForHuman`; that becomes a `Park` carrying the
        question, which `_raise_hands` puts in front of the operator.
        """
        state = DAGState.from_dict(
            await self._db.load_dag_state(task.id, dag_name=dag.name)
        )

        async def checkpoint(current: DAGState) -> None:
            await self._db.save_dag_state(
                task.id, dag_name=dag.name, results=current.to_dict()
            )

        from friday.dag.workflows import DAG_DEPS_EXTRA, DAG_SERVERS

        runner = DAGRunner(
            dag,
            deps=DAGDeps(
                task=task,
                db=self._db,
                servers=dict(DAG_SERVERS),
                extra=dict(DAG_DEPS_EXTRA.get(task.type, {})),
            ),
            state=state,
            on_checkpoint=checkpoint,
        )

        try:
            final = await runner.run()
        except PauseForHuman as pause:
            await self._db.save_dag_state(
                task.id,
                dag_name=dag.name,
                results=runner.state.to_dict(),
                paused_at_node=pause.node or dag.name,
                paused_question=str(pause),
            )
            log.info("task %d: %s paused — %s", task.id, dag.name, pause)
            return Park(str(pause))
        except Exception as exc:  # noqa: BLE001 - a graph failure becomes work
            log.warning("task %d: %s failed — %s", task.id, dag.name, exc)
            return Park(f"{dag.name} failed: {exc}")

        return self._outcome(dag, final, runner.trail)

    @staticmethod
    def _outcome(dag, final: DAGState, trail: list[str]) -> Action:
        """What the graph decided, as an action.

        Read backwards along the path the run actually took, not along the
        order the nodes were declared in. A graph often ends with bookkeeping
        — an audit line, a cleanup — declared after the node that decides, and
        letting declaration order answer means that bookkeeping silently
        discards the reply.

        A graph that walked its whole path without producing an `Action` has
        not said what to send, and parking is the honest answer. Inventing a
        reply out of a value the graph never meant as one is not.
        """
        for name in reversed(trail):
            if not final.has(name):
                continue
            result = final[name]
            if isinstance(result, (Ask, Reply, Park)):
                return result
        return Park(f"{dag.name} finished without deciding what to send")

    async def _move(self, task: Task, state: str) -> Task:
        await self._db.move_task(task.id, state)
        from dataclasses import replace

        return replace(task, state=state)


def _stuck(task: Task) -> str:
    """What it is, and enough of what it knows to judge without opening
    anything."""
    known = ", ".join(f"{k}: {v}" for k, v in sorted(task.params.items()) if v)
    return f"{task.type} #{task.id} — {known or 'nothing extracted'}"
