"""build-the-spine ticket 14 — the spine pass.

One DBOS workflow per pass, `task-<id>/pass-<n>`: intake → acknowledge (once
per task) → plan → run → deliver. An `Ask` ends the pass; the reply starts
pass n+1, which continues the asking step when the placement held and
replans when it moved. Carries `build-the-loop` ticket 04's acceptance
(board `domains-plug-in`, ticket 14).

Real DBOS, real store, real runner and `run_agent`; the Planner and the agent
are scripted (`tests/spine_demo.py`).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from friday.kernel.dag import adapter
from friday.kernel.domain.states import TaskState
from friday.kernel.pool.pool import Pool
from friday.kernel.spine.deliver import MAX_ASKS_PER_TASK
from friday.kernel.spine.workflow import pass_id, run_pass
from friday.sdk.outbox import Kind
from friday.sdk.testing import ScriptedModel
from friday.store.db import Database
from tests.spine_demo import (
    PLAN,
    Reads,
    Responder,
    asked,
    build_spine,
    found,
    open_task,
    planned,
    read,
    said,
)


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    from friday.kernel.harness import harness as harness_module

    monkeypatch.setattr(harness_module, "PROVIDER_BACKOFF_SECONDS", 0.0)


@pytest.fixture
def dbos():
    tmp = tempfile.mkdtemp()
    yield f"{tmp}/system.db"
    adapter.shutdown()


def _up(spine, system_db: str) -> Pool:
    """Register the pass the way the composition root does, then launch."""
    adapter.register_pass(run_pass, spine.steps())
    adapter.launch("friday-spine-test", system_db)
    return Pool(db=spine.db, auto_ask=spine.auto_ask, spine=spine)


async def _kinds(db) -> list[str]:
    return [str(r.kind) for r in await db.outbound()]


# ---- one pass ----------------------------------------------------------------


async def test_a_pass_acknowledges_plans_runs_and_proposes_a_reply(db, dbos):
    reads = Reads()
    responder = Responder()
    spine = build_spine(
        db,
        planner=planned(PLAN),
        diagnose=ScriptedModel([read(), found()]),
        reads=reads,
        responder=responder,
    )
    pool = _up(spine, dbos)
    task = await open_task(db)

    (acted,) = await pool.run_once()

    assert acted.state == TaskState.REVIEW
    assert await _kinds(db) == ["acknowledged", "reply", "approval_card"]
    ack, reply, card = await db.outbound()
    assert ack.text == "looking at users"
    assert reply.text == "email không hợp lệ" and card.approves == reply.id
    (given,) = responder.given
    assert given["reads"]["p1"].refs == ["L1"]
    assert given["intake"].domain.service == "users"
    (version,) = await db.plans(task.id)
    assert (version.version, version.cause, version.pass_no) == (1, "first", 1)
    assert version.placement == ["prod", "users"] and version.hash is not None
    status = await adapter.status(pass_id(task.id, 1))
    assert status == "done"


async def test_an_ungrounded_answer_is_handed_over_not_drafted(db, dbos):
    """The grounding gate (`AgentSpec.check`): a ref naming no line read."""
    responder = Responder()
    spine = build_spine(
        db,
        planner=planned(PLAN),
        diagnose=ScriptedModel([read(), found(refs=("L9",))]),
        reads=Reads(),
        responder=responder,
    )
    pool = _up(spine, dbos)
    task = await open_task(db)

    await pool.run_once()

    assert (await db.task(task.id)).state == TaskState.NEEDS_HUMAN
    assert responder.given == []
    assert "ungrounded" in (await db.pauses_for([task.id]))[task.id]


async def test_a_pass_run_again_under_its_id_sends_nothing_twice(db, dbos):
    spine = build_spine(
        db,
        planner=planned(PLAN),
        diagnose=ScriptedModel([read(), found()]),
        reads=Reads(),
    )
    _up(spine, dbos)
    task = await open_task(db)

    first = await adapter.run_pass(task.id, 1, pass_id(task.id, 1))
    again = await adapter.run_pass(task.id, 1, pass_id(task.id, 1))

    assert first == again == TaskState.REVIEW
    assert await _kinds(db) == ["acknowledged", "reply", "approval_card"]


async def test_a_crash_mid_pass_resumes_the_same_pass(db, dbos, tmp_path):
    """A child process runs pass 1 and dies inside the agent's read. The
    restart recovers `task-1/pass-1`: intake, acknowledge and plan are not run
    again (the Planner is not asked twice), the run step is, and nothing is
    sent twice."""
    app_db = str(tmp_path / "app.db")
    setup = await Database.connect(app_db, create=True)
    await open_task(setup)
    await setup.close()
    root = Path(__file__).resolve().parent.parent

    child = subprocess.run(
        [sys.executable, str(root / "tests/spine_demo.py"), app_db, dbos],
        capture_output=True,
        text=True,
        timeout=90,
        cwd=str(root),
        env={**os.environ, "PYDANTIC_AI_NO_BANNER": "1", "PYTHONPATH": str(root)},
        check=False,
    )
    assert child.returncode == 1, f"the child did not crash: {child.stderr[-800:]}"

    store = await Database.connect(app_db)
    try:
        assert await _kinds(store) == ["acknowledged"]
        planner = planned(PLAN)
        spine = build_spine(
            store,
            planner=planner,
            diagnose=ScriptedModel([read(), found()]),
            reads=Reads(),
        )
        _up(spine, dbos)  # recovery re-enters the pass the child left
        assert await adapter.result(pass_id(1, 1)) == TaskState.REVIEW

        assert await _kinds(store) == ["acknowledged", "reply", "approval_card"]
        assert planner.calls == [], "the plan step was memoized"
        assert len(await store.plans(1)) == 1
    finally:
        await store.close()


# ---- the reporter replies (build-the-loop ticket 04) ------------------------


async def test_an_unchanged_placement_continues_the_asking_step(db, dbos):
    """`Lnn` stable, reads not repeated: pass 2 continues the run that asked,
    from its history and its `Evidence`, with the reply as the brief."""
    reads = Reads()
    planner = planned(PLAN)
    diagnose = ScriptedModel([read(), asked(), found(refs=("L1",))])
    spine = build_spine(db, planner=planner, diagnose=diagnose, reads=reads)
    pool = _up(spine, dbos)
    task = await open_task(db)

    await pool.run_once()
    assert (await db.task(task.id)).state == TaskState.WAITING_FOR_DETAILS
    (question,) = [r for r in await db.outbound() if r.kind == Kind.ASK_FOR_DETAILS]
    assert question.text == "which user?", "sent as the agent wrote it"

    await said(db, task.id, "m-reply", "user 42, service users", minutes=5)
    await db.move_task(task.id, TaskState.PENDING)
    (acted,) = await pool.run_once()

    assert acted.state == TaskState.REVIEW
    assert (await db.task(task.id)).pass_no == 2
    assert reads.calls == 1, "the read was not repeated"
    assert len(planner.calls) == 1, "no replan: the placement held"
    continued = diagnose.calls[-1].input
    assert any("L1 | POST /users 400" in str(m) for m in continued), (
        "the continued run still sees L1 as it was shown"
    )
    assert "user 42" in str(continued[-1])
    assert (await _kinds(db)).count("acknowledged") == 1


async def test_a_reply_that_moves_the_placement_replans_and_reinvestigates(db, dbos):
    reads = Reads()
    planner = planned(PLAN, PLAN)
    diagnose = ScriptedModel([read(), asked(), read(call="r1"), found(call="a2")])
    spine = build_spine(db, planner=planner, diagnose=diagnose, reads=reads)
    pool = _up(spine, dbos)
    task = await open_task(db)

    await pool.run_once()
    await said(db, task.id, "m-reply", "à nhầm, trên staging", minutes=5)
    await db.move_task(task.id, TaskState.PENDING)
    await pool.run_once()

    assert (await db.task(task.id)).state == TaskState.REVIEW
    assert len(planner.calls) == 2, "replanned"
    assert reads.calls == 2, "investigated again, from nothing"
    versions = await db.plans(task.id)
    assert [(v.version, v.cause) for v in versions] == [(1, "first"), (2, "reply")]
    assert versions[1].placement == ["staging", "users"]


# ---- the bounds ---------------------------------------------------------------


async def _asked_before(db, task_id: int, n: int) -> None:
    for i in range(n):
        await db.queue_outbound(
            task_id=task_id,
            conversation=(await db.task(task_id)).conversation,
            kind=Kind.ASK_FOR_DETAILS,
            sender="discord_user",
            text=f"question {i}",
        )


async def test_asks_run_out_and_the_task_goes_to_the_operator(db, dbos):
    spine = build_spine(
        db,
        planner=planned(PLAN),
        diagnose=ScriptedModel([read(), asked()]),
        reads=Reads(),
    )
    pool = _up(spine, dbos)
    task = await open_task(db)
    await _asked_before(db, task.id, MAX_ASKS_PER_TASK)

    await pool.run_once()

    assert (await db.task(task.id)).state == TaskState.NEEDS_HUMAN
    assert (await db.pauses_for([task.id]))[task.id].startswith("asks_exhausted")
    asks = [r for r in await db.outbound() if r.kind == Kind.ASK_FOR_DETAILS]
    assert len(asks) == MAX_ASKS_PER_TASK, "no fourth question"


async def test_a_hand_back_resets_the_asks_and_plans_afresh(db, dbos):
    planner = planned(PLAN, PLAN)
    spine = build_spine(
        db,
        planner=planner,
        diagnose=ScriptedModel([read(), asked(), read(call="r1"), asked(call="q2")]),
        reads=Reads(),
    )
    pool = _up(spine, dbos)
    task = await open_task(db)
    await _asked_before(db, task.id, MAX_ASKS_PER_TASK)
    await pool.run_once()  # asks_exhausted → needs_human

    await db.move_task(task.id, TaskState.PENDING)  # the operator hands it back
    moved = await db.task(task.id)
    assert (moved.pass_no, moved.pass_cause) == (2, "hand_back")
    await pool.run_once()

    assert (await db.task(task.id)).state == TaskState.WAITING_FOR_DETAILS
    assert len(planner.calls) == 2, "a fresh plan"
    versions = await db.plans(task.id)
    assert [v.cause for v in versions] == ["first", "hand_back"]


async def test_a_hand_over_is_not_reused_after_a_hand_back(db, dbos):
    """The step that handed over runs again on the old ground."""
    reads = Reads()
    diagnose = ScriptedModel(
        [read(), [_hand_over("cannot tell")], read(call="r1"), found(call="a2")]
    )
    spine = build_spine(db, planner=planned(PLAN, PLAN), diagnose=diagnose, reads=reads)
    pool = _up(spine, dbos)
    task = await open_task(db)
    await pool.run_once()
    assert (await db.task(task.id)).state == TaskState.NEEDS_HUMAN

    await db.move_task(task.id, TaskState.PENDING)
    await pool.run_once()

    assert (await db.task(task.id)).state == TaskState.REVIEW
    assert reads.calls == 2


def _hand_over(reason: str):
    from friday.sdk.testing import function_call

    return function_call("hand_over", {"reason": reason}, call_id="h1")


async def test_a_reporter_message_mid_pass_holds_the_question(db, dbos):
    """They spoke while the agent was working: the question may already be
    answered, so it is not sent; pass n+1 continues with what they said."""
    task_holder: list = []

    class Talking(Reads):
        def spec(self):
            inner = super().spec()

            def factory(run):
                (read_line,) = inner.factory(run)

                async def talk_then_read(n: int) -> str:
                    """Read one log line.

                    Args:
                        n: which line, from 0.
                    """
                    await said(db, task_holder[0], "m-mid", "user 42 btw", minutes=1)
                    return read_line.fn(n)

                from friday.sdk.toolset import tool

                return [tool(talk_then_read, name="read_line")]

            return type(inner)(name=inner.name, description="d", factory=factory)

    diagnose = ScriptedModel([read(), asked(), found()])
    spine = build_spine(
        db,
        planner=planned(PLAN),
        diagnose=diagnose,
        reads=Talking(),
    )
    pool = _up(spine, dbos)
    task = await open_task(db)
    task_holder.append(task.id)

    await pool.run_once()

    after = await db.task(task.id)
    assert after.state == TaskState.PENDING
    assert (after.pass_no, after.pass_cause) == (2, "reply")
    assert Kind.ASK_FOR_DETAILS not in await _kinds(db)

    await pool.run_once()  # pass 2 continues the question with what they said

    assert "user 42 btw" in str(diagnose.calls[-1].input[-1])
    assert (await db.task(task.id)).state == TaskState.REVIEW


# ---- a step re-run after a crash between its commit and DBOS's record ---------


async def test_deliver_refuses_a_pass_the_task_has_left(db):
    task = await open_task(db)
    row = {
        "conversation": task.conversation,
        "kind": Kind.REPLY,
        "sender": "discord_user",
        "text": "x",
    }

    first = await db.deliver_pass(
        task_id=task.id, pass_no=1, state=TaskState.REVIEW, rows=[row]
    )
    again = await db.deliver_pass(
        task_id=task.id, pass_no=1, state=TaskState.REVIEW, rows=[row]
    )

    assert (first, again) == (TaskState.REVIEW, None)
    assert await _kinds(db) == ["reply"]


async def test_a_plan_step_run_again_in_its_pass_reuses_its_version(db):
    from friday.kernel.spine.versions import choose_plan

    planner = planned(PLAN, PLAN)
    spine = build_spine(db, planner=planner, diagnose=ScriptedModel([]), reads=Reads())
    task = await open_task(db)
    context = (await spine.intake(task.id)).context
    planning = spine._planning(await db.task(task.id), context)

    await choose_plan(db, planning, spine.agents, pass_no=1, cause="first")
    # A hand-back pass writes a fresh version; re-run, it must not write two.
    first = await choose_plan(db, planning, spine.agents, pass_no=2, cause="hand_back")
    again = await choose_plan(db, planning, spine.agents, pass_no=2, cause="hand_back")

    assert len(planner.calls) == 2
    assert again.frozen == first.frozen
    assert [v.cause for v in await db.plans(task.id)] == ["first", "hand_back"]


async def test_a_replan_run_again_in_its_pass_reuses_its_version(db):
    from friday.kernel.spine.versions import choose_plan, write_replan
    from friday.sdk.actions import Replan

    planner = planned(PLAN, PLAN, PLAN)
    spine = build_spine(db, planner=planner, diagnose=ScriptedModel([]), reads=Reads())
    task = await open_task(db)
    context = (await spine.intake(task.id)).context
    planning = spine._planning(await db.task(task.id), context)
    current = (
        await choose_plan(db, planning, spine.agents, pass_no=1, cause="first")
    ).frozen
    signal = Replan(reason="wrong way", found="L1")

    first = await write_replan(db, planning, current, {}, signal, pass_no=1)
    again = await write_replan(db, planning, current, {}, signal, pass_no=1)

    assert len(planner.calls) == 2
    assert again == first
    assert [v.cause for v in await db.plans(task.id)] == ["first", "replan"]


async def test_a_pass_that_fails_goes_to_a_person_not_round_again(db, dbos):
    """DBOS keeps a failed pass failed; joining it every pool pass would fail
    the same way for ever, and take `_raise_hands` down with it."""
    from dataclasses import replace

    from tests.spine_demo import ACTION, ACTION_NAME

    spine = build_spine(
        db, planner=planned(PLAN), diagnose=ScriptedModel([]), reads=Reads()
    )
    spine.actions = {ACTION_NAME: replace(ACTION, acknowledge=lambda c: 1 / 0)}
    pool = _up(spine, dbos)
    task = await open_task(db)

    await pool.run_once()
    await pool.run_once()

    assert (await db.task(task.id)).state == TaskState.NEEDS_HUMAN
    assert (await db.pauses_for([task.id]))[task.id].startswith("pass_failed")
    assert Kind.HELP_WANTED in await _kinds(db)
