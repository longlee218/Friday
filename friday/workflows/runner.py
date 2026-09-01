"""Running workflows over tasks that are ready for one."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from friday.dag import DAGDeps, DAGRunner, DAGState, PauseForHuman
from friday.dag.router import dag_for
from friday.store.db import Database
from friday.domain.tasks import TaskState
from friday.domain.models import Task
from friday.outbox import Kind
from friday.workflows import (
    PARAMS,
    Action,
    Ask,
    Park,
    Reply,
    plan_by_required_parameters,
    prepare,
)

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

    @classmethod
    def build(cls, config, *, db: Database, responder=None) -> "WorkflowRunner":
        """The loop, built from the `workflows:` block.

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
            debounce_seconds=config.workflows.debounce_seconds,
        )

    def __init__(
        self,
        *,
        db: Database,
        auto_ask: bool,
        responder=None,
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
        # Asked of the responder rather than passed in beside it. It is the
        # responder's knob; this loop only fetches what it is told to fetch.
        self._tone_examples = getattr(responder, "tone_examples", 8)
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
        pauses = await self._db.dag_pauses(
            {task.id: _fingerprint(task.params) for task in tasks}
        )
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
            text = _stuck(task, pauses.get(task.id))
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
        """Fill in, check, then route. In that order, for every task type.

        The order is the point. Extraction and validation used to live inside
        `plan()`, which the graph route skips — so the one type that has both
        an extractor and rules got neither, and a malformed correlationId
        reached the graph looking findable.
        """
        params_type = PARAMS.get(task.type)
        if params_type is None:
            return Park(f"unknown task type {task.type!r}")

        try:
            params = params_type(**task.params)
        except TypeError as exc:
            # Stored parameters that no longer fit their type — a schema change
            # landing on rows written before it. Work, not a crash.
            return Park(f"cannot read {task.type} parameters: {exc}")

        params, problem = await prepare(
            task.type, params, text=await self._db.original_text_for(task.id)
        )
        task = await self._remember(task, params)
        if problem is not None:
            return problem

        dag = dag_for(task.type)
        if dag is not None:
            return await self._run_dag(dag, task)
        return plan_by_required_parameters(task.type, params)

    async def _remember(self, task: Task, params) -> Task:
        """Write back what extraction filled in, if it filled anything in.

        Persisted rather than recomputed, for three reasons that all point the
        same way. The graph reads `task.params` and would otherwise never see
        the extracted fields. The board shows what the system believes, and
        that should be what it acted on. And the graph's state is keyed on a
        fingerprint of these parameters — recomputing them from the model on
        every poll would let a differently-worded extraction throw away a
        run's work for no reason.
        """
        from dataclasses import asdict, replace

        filled = {**task.params, **asdict(params)}
        if filled == task.params:
            return task
        await self._db.set_task_params(task.id, filled)
        log.info("task %d: parameters filled in from the original message", task.id)
        return replace(task, params=filled)

    async def _run_dag(self, dag, task: Task) -> Action:
        """Run the graph registered for this task type.

        The runner records each node before starting the next, so a crash
        mid-graph resumes here rather than starting over. A node that cannot
        decide raises `PauseForHuman`; that becomes a `Park` carrying the
        question, which `_raise_hands` puts in front of the operator.
        """
        fingerprint = _fingerprint(task.params)
        state = DAGState.from_dict(
            await self._db.load_dag_state(
                task.id, dag_name=dag.name, params_fingerprint=fingerprint
            )
        )

        async def checkpoint(current: DAGState) -> None:
            await self._db.save_dag_state(
                task.id,
                dag_name=dag.name,
                results=current.to_dict(),
                params_fingerprint=fingerprint,
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
                params_fingerprint=fingerprint,
                # The node, not the graph. The trail's last entry is the node
                # that was running when it raised — appended before the node
                # runs, precisely so a pause can be attributed.
                paused_at_node=pause.node
                or (runner.trail[-1] if runner.trail else dag.name),
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


def _fingerprint(params: dict) -> str:
    """A stable digest of the parameters a graph ran against.

    Empty values are dropped, because `""` and `None` and absent are the same
    absence for a `str | None` field, and discarding a graph's work over that
    distinction would cost tool calls for nothing.

    That is true of every parameter type there is today, and stops being true
    the first time one is a bool or a number: `False`, `0` and `[]` would then
    read as "not supplied", and answering a question with `False` would not
    invalidate the state computed without it. Revisit this line when a
    `Params` field is not `str | None`.

    What is left is serialised as JSON with sorted keys rather than joined
    into a string. Joining `f"{key}={value}"` made `{"a": "b=c"}` and
    `{"a=b": "c"}` the same fingerprint, and `1` the same as `"1"` — both
    unreachable today, because every parameter is a `str | None` field named
    by the dataclass. But this function is handed the raw JSON-decoded dict,
    not the dataclass, so the type discipline it was relying on is not
    enforced at its own edge. JSON does not need it to be.
    """
    from hashlib import blake2b

    material = json.dumps(
        {key: value for key, value in params.items() if value},
        sort_keys=True,
        allow_nan=False,
        default=repr,
    )
    return blake2b(material.encode(), digest_size=16).hexdigest()


def _stuck(task: Task, pause: tuple[str, str] | None = None) -> str:
    """What it is, and enough of what it knows to judge without opening
    anything."""
    known = ", ".join(f"{k}: {v}" for k, v in sorted(task.params.items()) if v)
    line = f"{task.type} #{task.id} — {known or 'nothing extracted'}"
    if pause is not None and pause[1]:
        line += f"\n{pause[0]}: {pause[1]}"
    return line
