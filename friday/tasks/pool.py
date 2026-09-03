"""The pool: pulls pending tasks and hosts their graphs.

Four things, every pass — nothing else: stand down for a task the operator
answered themselves, announce to the operator whatever nobody can act on, host
the graph for whatever tasks are pending, and turn what came back into rows.
Deciding *what* to do with a task is the graph's business (`friday/dag/`); this
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
import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from friday.dag import DAGDeps, DAGRunner, DAGState
from friday.dag.router import dag_for
from friday.store.db import Database
from friday.domain.actions import Action, Ask, HandOver, Reply
from friday.domain.states import TaskState
from friday.domain.models import PARAMS, Task
from friday.outbox import Kind

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
    def build(cls, config, *, db: Database, responder=None) -> "Pool":
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
        )

    def __init__(
        self,
        *,
        db: Database,
        auto_ask: bool,
        responder=None,
        max_asks: int = 3,
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
        self._sender = sender
        self._approver = approver
        self._batch_size = batch_size

    async def run_forever(self, poll_interval_seconds: float = 2.0) -> None:
        while True:
            if not await self.run_once():
                await asyncio.sleep(poll_interval_seconds)

    async def run_once(self) -> list[Task]:
        await self._stand_down()
        acted: list[Task] = []
        for task in await self._db.tasks_in_state(PENDING, self._batch_size):
            acted.append(await self._act(task))
        await self._raise_hands()
        return acted

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

    async def _route(self, task: Task, action: Action) -> Task:
        """Given a decision, do what it says. Shared by the ordinary pass and
        by `decide_pending_action` — an approval resuming a graph reaches the
        same three outcomes a fresh pass does, and should be routed the same
        way once it has one."""
        if isinstance(action, Reply):
            return await self._propose(task, action.text)

        if isinstance(action, Ask):
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
            params=_as_params(task),
            channel_id=task.conversation.channel_id,
            stranger=await self._stranger(task),
            context=await self._db.relevant_messages(task.conversation),
            tone=await self._db.tone_examples(limit=self._tone_examples),
        )
        return template if draft is None else draft.text

    async def _stranger(self, task: Task) -> bool:
        """Nobody the operator has written to before, and nobody they have
        written down. Learning a voice from history fails exactly here — no
        history, so the average, and the average is how they write to their
        own team — so the default has to be the safer register, not the
        average one."""
        who = await self._db.reporter_of(task.id)
        if who is None:
            return False
        author_id, name = who
        named = getattr(self._responder, "knows", lambda *_: False)(
            task.conversation.channel_id, name
        )
        return not named and not await self._db.has_exchanged_with(author_id)

    async def _plan(self, task: Task) -> Action:
        """Route to this type's graph. Every classifiable type has one — see
        `register_dags` — so there is no second way to decide what to do with
        a task any more."""
        params_type = PARAMS.get(task.type)
        if params_type is None:
            return HandOver(f"unknown task type {task.type!r}")

        try:
            params_type(**task.params)
        except TypeError as exc:
            # Stored parameters that no longer fit their type — a schema change
            # landing on rows written before it. Work, not a crash.
            return HandOver(f"cannot read {task.type} parameters: {exc}")

        dag = dag_for(task.type)
        assert dag is not None, f"{task.type!r} is in PARAMS but has no registered graph"
        return await self._run_dag(dag, task)

    async def _run_dag(self, dag, task: Task) -> Action:
        """Run the graph registered for this task type.

        Node 0 — `dag.entry`, `prepare` for every graph today — is run once,
        upfront, outside the checkpoint entirely: there is no fingerprint to
        load state *by* until node 0 has produced one, and it must run on
        every pass regardless of what is stored, because there may be a new
        message since the last one. If it says the report cannot be worked
        with, the graph never starts.

        Past that, the runner records each remaining node before starting the
        next, so a crash mid-graph resumes here rather than starting over. A
        node that cannot decide returns an `Ask` or `HandOver` like any node
        deciding the graph's answer; when that is how the run ends, which
        node said so and what it said are saved alongside the checkpoint, so
        `_raise_hands` can put the specific question in front of the operator
        rather than the task's bare type and parameters.
        """
        from friday.dag.router import DAG_DEPS_EXTRA, DAG_SERVERS

        deps = DAGDeps(
            task=task,
            db=self._db,
            servers=dict(DAG_SERVERS),
            extra=dict(DAG_DEPS_EXTRA.get(task.type, {})),
        )

        try:
            prepared = await dag.node(dag.entry).run(DAGState.empty(), deps)
        except Exception as exc:  # noqa: BLE001 - a graph failure becomes work
            log.warning("task %d: %s's %s failed — %s", task.id, dag.name, dag.entry, exc)
            return HandOver(f"{dag.name} failed: {exc}")

        if isinstance(prepared, (Ask, Reply, HandOver)):
            if isinstance(prepared, HandOver):
                # Node 0 deciding the answer is not special — a one-node
                # graph's only node is node 0 — so it gets the same
                # pause-recording treatment as any later node's `HandOver` does.
                await self._record_pause(
                    task.id,
                    dag,
                    results={},
                    fingerprint=_fingerprint(task.params),
                    node=dag.entry,
                    hand_over=prepared,
                )
            return prepared

        fingerprint = _fingerprint(_prepare_material(prepared))
        state = DAGState.from_dict(
            await self._db.load_dag_state(
                task.id, dag_name=dag.name, params_fingerprint=fingerprint
            )
        ).with_result(dag.entry, prepared)

        async def checkpoint(current: DAGState) -> None:
            await self._db.save_dag_state(
                task.id,
                dag_name=dag.name,
                results=_checkpointable(current, dag),
                params_fingerprint=fingerprint,
            )

        runner = DAGRunner(dag, deps=deps, state=state, on_checkpoint=checkpoint)

        try:
            final = await runner.run()
        except Exception as exc:  # noqa: BLE001 - a graph failure becomes work
            log.warning("task %d: %s failed — %s", task.id, dag.name, exc)
            return HandOver(f"{dag.name} failed: {exc}")

        outcome = self._outcome(dag, final, runner.trail)
        if isinstance(outcome, HandOver):
            # The run ended here rather than deciding something to send — an
            # `Ask`/`Reply` is still headed for the reporter or the outbox, so
            # only a `HandOver` is "stuck" in the sense the operator needs the
            # specific reason for. One more save alongside the ordinary
            # checkpoint the last node already wrote, so `_raise_hands` reads
            # this run's actual reason rather than the task's bare type.
            await self._record_pause(
                task.id,
                dag,
                results=_checkpointable(final, dag),
                fingerprint=fingerprint,
                node=_last_action_node(final, runner.trail) or dag.name,
                hand_over=outcome,
            )
        return outcome

    async def decide_pending_action(
        self, task_id: int, *, approve: bool, reason: str | None = None
    ) -> Task:
        """The operator's yes or no on a paused tool call (ticket 07).

        Nothing in this codebase calls this yet — Discord buttons or the
        board will, once they exist (tickets 18–20) — but the mechanism
        stands complete without them: resume in this process or a fresh one,
        decline without asking the model anything further, either way routed
        through `_route` exactly like a fresh pass's `Action` is.

        A stale approval — the task moved on since — is not something this
        checks for separately. `dag_state` is one row per task, replaced
        whole on every checkpoint: the moment any later pass runs at all, it
        overwrites `interruption` with whatever that pass left, `None` if
        nothing did. Reaching this with a *specific* interruption still on
        the row means nothing has touched it since — there is nothing later
        to be stale relative to.
        """
        stored = await self._db.dag_interruption(task_id)
        if stored is None:
            raise ValueError(f"task {task_id} has no pending approval")

        task = await self._db.task(task_id)
        if task is None:
            raise ValueError(f"task {task_id} does not exist")

        dag = dag_for(task.type)
        assert dag is not None, f"{task.type!r} is in PARAMS but has no registered graph"

        from friday.dag.router import DAG_DEPS_EXTRA, DAG_SERVERS

        node_name = stored["paused_at_node"]
        agent = DAG_DEPS_EXTRA.get(task.type, {}).get(node_name)

        if not approve:
            outcome: Action = HandOver(reason or "declined by the operator")
        elif agent is None:
            outcome = HandOver(f"{node_name} has no agent to resume with any more")
        else:
            # A fresh capture, not the one the original call closed over —
            # that object does not survive the process it was made in. If a
            # tool gets called during the resumed turns, it writes into this
            # one; if none does, `capture.action` just stays `None` and
            # `result.final_output` is read instead, the same fallback
            # `_fix_bug`'s own first attempt uses.
            from friday.dag.api_issue import ComposeCapture

            capture = ComposeCapture()
            result = await agent.resume(stored["interruption"], context=capture)
            if result is None:
                outcome = HandOver(f"could not resume: {agent.last_error}")
            elif result.interruptions:
                outcome = HandOver(
                    "needs approval again", interruption=agent.checkpoint(result)
                )
            elif capture.action is not None:
                outcome = await self._continue_from(
                    task, dag, node_name, stored, capture.action
                )
            else:
                resumed_text = (result.final_output or "").strip() or None
                outcome = await self._continue_from(
                    task, dag, node_name, stored, resumed_text
                )

        if isinstance(outcome, HandOver):
            await self._record_pause(
                task_id,
                dag,
                results=stored["results"],
                fingerprint=stored["params_fingerprint"],
                node=node_name,
                hand_over=outcome,
            )
        # An Ask or Reply means the graph continued past `node_name` —
        # `DAGRunner`'s own `on_checkpoint` already recorded that, interruption
        # cleared along with it, node by node, the same as any other run.
        return await self._route(task, outcome)

    async def _continue_from(
        self, task: Task, dag, node_name: str, stored: dict, resumed_value: Any
    ) -> Action:
        """The rest of the graph, picked up from exactly the node that
        stopped — not restarted, which would spend the earlier nodes' work
        again to reach the same call.

        `resumed_value` is whatever `node_name` resolved to: an `Action`
        (its agent called a tool while resuming, the same as any node's
        agent can) or plain text (it did not, and the caller already fell
        back the same way a fresh call would). Either way this only injects
        it and lets the graph's own edges decide what happens past it —
        `_fix_bug_ok`-style guards work the same on an injected value as on
        one the node just returned.
        """
        from friday.dag.router import DAG_DEPS_EXTRA, DAG_SERVERS

        state = (
            DAGState.from_dict(stored["results"])
            .with_result(dag.entry, PARAMS[task.type](**task.params))
            .with_result(node_name, resumed_value)
        )
        deps = DAGDeps(
            task=task,
            db=self._db,
            servers=dict(DAG_SERVERS),
            extra=dict(DAG_DEPS_EXTRA.get(task.type, {})),
        )
        fingerprint = stored["params_fingerprint"]

        async def checkpoint(current: DAGState) -> None:
            await self._db.save_dag_state(
                task.id,
                dag_name=dag.name,
                results=_checkpointable(current, dag),
                params_fingerprint=fingerprint,
            )

        runner = DAGRunner(dag, deps=deps, state=state, on_checkpoint=checkpoint)
        try:
            final = await runner.run()
        except Exception as exc:  # noqa: BLE001 - a graph failure becomes work
            log.warning("task %d: %s failed resuming from %s — %s", task.id, dag.name, node_name, exc)
            return HandOver(f"{dag.name} failed: {exc}")
        return self._outcome(dag, final, runner.trail)

    async def _record_pause(
        self, task_id: int, dag, *, results: dict, fingerprint: str, node: str, hand_over: HandOver
    ) -> None:
        """A run ended on a `HandOver`: save which node said so and what it said,
        alongside the state so far, so `_raise_hands` can read this run's
        actual reason rather than the task's bare type and parameters.

        `hand_over.interruption`, when set, is a tool call waiting on the
        operator's yes or no (ticket 07) — stored alongside so
        `decide_pending_action` can find it by task id later, in this
        process or another one.
        """
        await self._db.save_dag_state(
            task_id,
            dag_name=dag.name,
            results=results,
            params_fingerprint=fingerprint,
            paused_at_node=node,
            paused_question=hand_over.reason,
            interruption=hand_over.interruption,
        )

    @staticmethod
    def _outcome(dag, final: DAGState, trail: list[str]) -> Action:
        """What the graph decided, as an action.

        A graph that walked its whole path without producing an `Action` has
        not said what to send, and handing over is the honest answer. Inventing a
        reply out of a value the graph never meant as one is not.
        """
        node = _last_action_node(final, trail)
        if node is not None:
            return final[node]
        return HandOver(f"{dag.name} finished without deciding what to send")

    async def _move(self, task: Task, state: str) -> Task:
        await self._db.move_task(task.id, state)
        from dataclasses import replace

        return replace(task, state=state)


def _as_params(task: Task):
    """The task's parameters as their declared type, or None if they no longer
    fit it. Best effort: the responder is better off with no params than with
    a crash, and `_plan` has already handed over anything malformed."""
    params_type = PARAMS.get(task.type)
    if params_type is None:
        return None
    try:
        return params_type(**task.params)
    except TypeError:
        return None


def _prepare_material(prepared) -> dict:
    """Node 0's output, shaped for `_fingerprint`.

    A dataclass — every real `prepare`, `ApiIssueParams` included — becomes
    its fields. Anything already a mapping is used as-is, for a synthetic
    graph's node 0 that returns a plain dict. Anything else is wrapped, so a
    value that is not naturally a mapping gets a stable digest instead of
    crashing the graph before its first real node runs.
    """
    from dataclasses import is_dataclass

    if is_dataclass(prepared):
        return asdict(prepared)
    if isinstance(prepared, dict):
        return prepared
    return {"value": prepared}


def _last_action_node(final: DAGState, trail: list[str]) -> str | None:
    """Which node in the trail produced the graph's `Action`, reading
    backwards along the path the run actually took rather than the order the
    nodes were declared in. A graph often ends with bookkeeping — an audit
    line, a cleanup — declared after the node that decides, and letting
    declaration order answer means that bookkeeping silently discards the
    reply. `None` if no node on the trail produced one at all.
    """
    for name in reversed(trail):
        if final.has(name) and isinstance(final[name], (Ask, Reply, HandOver)):
            return name
    return None


def _checkpointable(state: DAGState, dag) -> dict:
    """What actually gets persisted: everything but node 0.

    Node 0 re-reads what the reporter has said on every pass; storing its
    result would let a restart skip a message that arrived after the last
    checkpoint. `_run_dag` never asks `DAGRunner` to run node 0 through the
    normal loop in the first place — this only has to keep it out of what
    gets written, in the two places a run's state is saved.
    """
    return {k: v for k, v in state.to_dict().items() if k != dag.entry}


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
    by the dataclass. But this function is handed a plain dict — `asdict()`
    of a graph's node 0 output, or the raw JSON-decoded storage — not the
    dataclass itself, so the type discipline it was relying on is not
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


def _stuck(
    task: Task,
    pause: tuple[str, str] | None = None,
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
    if pause is not None and pause[1]:
        line += f"\n{pause[0]}: {pause[1]}"
    if last_said:
        quoted = last_said.strip().replace("\n", "\n> ")
        line += f"\nthey last said:\n> {quoted}"
    return line
