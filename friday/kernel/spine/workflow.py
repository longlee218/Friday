"""The spine pass: one durable workflow per pass, `task-<id>/pass-<n>`
(build-the-spine ticket 14; board `domains-plug-in` ticket 14 as amended by
17, ticket 15 §1–2).

```
intake       always fresh (the DB only) — the context every step gets
acknowledge  the action's hook, once per task, queued without approval
plan         Planner + GatePlan, or the frozen plan kept (`versions.py`)
run          the runner walks it (`runner.py`)
deliver      outbox rows + the task's state, one transaction (`deliver.py`)
```

Each stage is one DBOS step, so a crash resumes the same pass id and nothing
done is done again; an `Ask` ends the pass — no workflow waits on a person —
and the reply starts pass n+1. The body here is plain code: `step(name,
*args)` is the adapter's durable step (`friday/kernel/dag/adapter.py`, the one
module that imports `dbos`), and `Spine` binds what the steps run with.
Over 200 lines because the five steps and their bindings are one unit.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from friday.kernel.config import AgentConfig, TierConfig
from friday.kernel.domain.state import FridayState
from friday.kernel.harness.run_agent import run_agent
from friday.kernel.outbox import DEFAULT_APPROVER, DEFAULT_SENDER, Kind
from friday.kernel.spine.brief import agent_input, reply_brief
from friday.kernel.spine.deliver import deliver
from friday.kernel.spine.intake import intake
from friday.kernel.spine.plan import AgentStep, DraftStep
from friday.kernel.spine.plan_gate import Frozen
from friday.kernel.spine.planner import Planning
from friday.kernel.spine.runner import Resume, Steps, run_plan
from friday.kernel.spine.versions import Planned, choose_plan, write_replan
from friday.sdk.action import Action
from friday.sdk.actions import Replan, Reply
from friday.sdk.agent import AgentSpec
from friday.sdk.evidence import Evidence
from friday.sdk.intake import IntakeContext
from friday.sdk.toolset import RunContext, ToolsetSpec

__all__ = ["Intaken", "Spine", "pass_id", "run_pass"]

log = logging.getLogger(__name__)

#: A durable step: `await step("plan", *args)`.
Step = Callable[..., Awaitable[Any]]


def pass_id(task_id: int, pass_no: int) -> str:
    return f"task-{task_id}/pass-{pass_no}"


@dataclass(frozen=True, slots=True)
class Intaken:
    """Intake's result: the context, and the newest reporter turn it read —
    what `deliver` holds a mid-pass message against."""

    context: IntakeContext
    heard_until: datetime | None


async def run_pass(task_id: int, pass_no: int, step: Step) -> str:
    """One pass, stage by stage; the state the task ends it in."""
    intaken = await step("intake", task_id)
    await step("acknowledge", task_id, intaken.context)
    planned: Planned = await step("plan", task_id, pass_no, intaken.context)
    outcome = planned.outcome
    if planned.frozen is not None:
        outcome = await step(
            "run",
            task_id,
            pass_no,
            intaken.context,
            planned.frozen,
            planned.replans_used,
            intaken.heard_until,
        )
    return await step(
        "deliver", task_id, pass_no, outcome, intaken.heard_until, planned.asks_since
    )


def _reported_at(task: Any) -> datetime:
    """When the reporter spoke: the task's creation, within seconds of the
    message that opened it."""
    at = task.created_at or datetime.now(UTC)
    return at if at.tzinfo else at.replace(tzinfo=UTC)


@dataclass
class Spine:
    """What every pass runs with, bound once at boot. `actions` are the
    actions this spine runs (the rest stay on the DAG path until ticket 16);
    `enrichers` are the domains' Intake enrichers, by plugin id; `tiers` the
    agents' tiers; `planner` the Planner's resolved config. `models` (scripted
    transports, by agent name — `planner` for the Planner) and `record` pass
    through to the Harness."""

    db: Any
    actions: Mapping[str, Action]
    agents: Mapping[str, AgentSpec]
    toolsets: Mapping[str, ToolsetSpec]
    tiers: Mapping[str, TierConfig]
    planner: AgentConfig
    enrichers: Mapping[str, Any] = field(default_factory=dict)
    servers: Mapping[str, Any] = field(default_factory=dict)
    responder: Any = None
    record: Any = None
    models: Mapping[str, Any] = field(default_factory=dict)
    sender: str = DEFAULT_SENDER
    approver: str = DEFAULT_APPROVER
    auto_ask: bool = False

    def runs(self, task_type: str) -> bool:
        return task_type in self.actions

    def steps(self) -> dict[str, Callable[..., Awaitable[Any]]]:
        return {
            "intake": self.intake,
            "acknowledge": self.acknowledge,
            "plan": self.plan,
            "run": self.run,
            "deliver": self.deliver,
        }

    async def intake(self, task_id: int) -> Intaken:
        task = await self.db.task(task_id)
        context = await intake(
            self.db,
            task_id=task_id,
            channel_id=task.conversation.channel_id,
            reported_at=_reported_at(task).isoformat(),
            enricher=self.enrichers.get(task.type.partition(".")[0]),
        )
        return Intaken(context, await self.db.last_reporter_turn_at(task_id))

    async def acknowledge(self, task_id: int, context: IntakeContext) -> None:
        """Once per task, whatever pass (ticket 15 §1): a reply's Intake
        re-run never sends a second one."""
        task = await self.db.task(task_id)
        hook = self.actions[task.type].acknowledge
        if hook is None or not self.sender:
            return
        if await self.db.outbound_count(task_id, kind=Kind.ACKNOWLEDGED):
            return
        text = hook(context)
        if not text:
            return
        await self.db.queue_outbound(
            task_id=task_id,
            conversation=task.conversation,
            kind=Kind.ACKNOWLEDGED,
            sender=self.sender,
            text=text,
            reply_to=await self.db.last_mention_in(task.conversation),
        )
        log.info("task %d: acknowledged", task_id)

    async def plan(self, task_id: int, pass_no: int, context: IntakeContext) -> Planned:
        task = await self.db.task(task_id)
        return await choose_plan(
            self.db,
            self._planning(task, context),
            self.agents,
            pass_no=pass_no,
            cause=task.pass_cause,
        )

    async def run(
        self,
        task_id: int,
        pass_no: int,
        context: IntakeContext,
        frozen: Frozen,
        replans_used: int,
        heard_until: datetime | None = None,
    ) -> Any:
        task = await self.db.task(task_id)
        planning = self._planning(task, context)

        async def planner(current: Frozen, results: Mapping[str, Any], signal: Replan):
            return await write_replan(
                self.db, planning, current, results, signal, pass_no=pass_no
            )

        async def replied(since: datetime | None) -> str:
            return "\n".join(await self.db.original_turns_for(task_id, since=since))

        end = await run_plan(
            self.db,
            frozen,
            agents=self.agents,
            placement_identity=context.identity,
            steps=Steps(
                agent=lambda step, reads, resume: self._agent(
                    task, context, step, reads, resume
                ),
                draft=lambda step, reads: self._draft(task, context, step, reads),
                planner=planner,
                replied=replied,
                heard_until=heard_until,
            ),
            replans_used=replans_used,
            pass_no=pass_no,
        )
        return end.outcome

    async def deliver(
        self,
        task_id: int,
        pass_no: int,
        outcome: Any,
        heard_until: datetime | None,
        asks_since: datetime | None,
    ) -> str:
        return await deliver(
            self.db,
            await self.db.task(task_id),
            pass_no,
            outcome,
            heard_until=heard_until,
            asks_since=asks_since,
            sender=self.sender,
            approver=self.approver,
            auto_ask=self.auto_ask,
        )

    def _planning(self, task: Any, context: IntakeContext) -> Planning:
        return Planning(
            config=self.planner,
            task_id=task.id,
            action=self.actions[task.type],
            intake=context,
            agents=self.agents,
            toolsets=self.toolsets,
            state=FridayState.for_conversation(
                task.conversation, agent="planner"
            ).for_task(task.id),
            model=self.models.get("planner"),
            record=self.record,
        )

    async def _agent(
        self,
        task: Any,
        context: IntakeContext,
        step: AgentStep,
        reads: Mapping[str, Any],
        resume: Resume | None,
    ) -> Any:
        """One agent step: its granted toolsets over a fresh `Evidence`, or
        continued from its stored `Ask` with the reporter's reply."""
        spec = self.agents[step.agent]
        run = RunContext(
            task_id=task.id,
            domain=context.domain,
            evidence=Evidence(),
            mcp={},
            reported_at=datetime.fromisoformat(context.reported_at),
        )
        return await run_agent(
            spec,
            self.tiers[spec.tier],
            self.actions[task.type].contract,
            [self.toolsets[name] for name in step.toolsets if name in self.toolsets],
            run,
            reply_brief(resume.reply)
            if resume
            else agent_input(step.brief, context, reads),
            resume.ask if resume else None,
            model=self.models.get(spec.name),
            record=self.record,
            servers=self.servers,
        )

    async def _draft(
        self,
        task: Any,
        context: IntakeContext,
        step: DraftStep,
        reads: Mapping[str, Any],
    ) -> Reply:
        """The `draft` step: the responder's reply, or a failed attempt."""
        if self.responder is None:
            raise RuntimeError("no responder is configured to draft the reply")
        written = await self.responder.reply(
            action=task.type,
            intake=context,
            reads=reads,
            state=FridayState.for_conversation(task.conversation, agent="responder")
            .for_task(task.id)
            .about_message(await self.db.source_message_of(task.id)),
            stranger=await _stranger(self.db, task),
            context=await self.db.relevant_messages(task.conversation),
            tone=await self.db.tone_examples(limit=self.responder.tone_examples),
        )
        if written is None:
            raise RuntimeError("the responder wrote no reply")
        return Reply(written.text)


async def _stranger(db: Any, task: Any) -> bool:
    """Nobody the operator has written to before, and nobody they have
    written down — the responder then takes the safer register."""
    who = await db.reporter_of(task.id)
    if who is None:
        return False
    author_id, _ = who
    named = await db.knows_person(task.conversation.channel_id, author_id)
    return not named and not await db.has_exchanged_with(author_id)
