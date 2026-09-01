"""Ticket 32 — a workflow is a graph, and it survives a restart.

Two properties earn this module its place, and every test below guards one of
them: a completed node does not run twice, and a node that cannot decide can
say so instead of guessing.
"""

from __future__ import annotations

import pytest

from friday.dag import DAG, DAGDeps, DAGRunner, Edge, Node
from friday.dag.pause import PauseForHuman
from friday.dag.router import EDGE_ROUTER, dag_for, register_dag
from friday.dag.state import UNSTORABLE, DAGState, MissingNodeResult


def node(name: str, produces=None, *, record: list | None = None) -> Node:
    """A node that records that it ran and returns a fixed value."""

    async def run(state: DAGState, deps: DAGDeps):
        if record is not None:
            record.append(name)
        return produces if produces is not None else f"{name}-ran"

    return Node(name=name, run=run)


# --- state ---------------------------------------------------------------


def test_state_is_immutable_a_write_returns_a_new_state():
    """A node cannot reach back and change what an earlier one recorded —
    that is what makes the checkpoint honest."""
    first = DAGState.empty()
    second = first.with_result("a", 1)

    assert first.results == {}
    assert second["a"] == 1


def test_reading_a_node_that_never_ran_raises_rather_than_returning_none():
    """A None here becomes a bad prompt three nodes later. The mistake is in
    the edges, and it should say so at the point of the mistake."""
    with pytest.raises(MissingNodeResult, match="analyze"):
        DAGState.empty()["analyze"]


def test_state_survives_a_round_trip_through_storage():
    state = DAGState.empty().with_result("a", {"k": "v"}).with_result("b", 2)

    restored = DAGState.from_dict(state.to_dict())

    assert restored["a"] == {"k": "v"}
    assert restored["b"] == 2


def test_a_malformed_stored_state_starts_empty_rather_than_wedging_the_task():
    assert DAGState.from_dict(None).results == {}
    assert DAGState.from_dict("not a dict").results == {}  # type: ignore[arg-type]


def test_a_value_that_will_not_serialise_is_stored_as_a_marker_not_dropped():
    """The live state holds whatever a node returned; storage cannot. A node
    that returned a domain object still counts as complete on resume — that
    is the whole point of the checkpoint — and the marker says what it was
    rather than leaving a hole."""

    class NotJson:
        pass

    state = DAGState.empty().with_result("a", "fine").with_result("b", NotJson())

    stored = state.to_dict()

    assert stored["a"] == "fine"
    assert stored["b"] == {UNSTORABLE: "NotJson"}
    # The live value is untouched — only the projection is lossy.
    assert isinstance(state["b"], NotJson)


def test_a_node_marked_unstorable_still_counts_as_complete_on_resume():
    class NotJson:
        pass

    restored = DAGState.from_dict(
        DAGState.empty().with_result("expensive", NotJson()).to_dict()
    )

    assert restored.has("expensive")


# --- construction guards ---------------------------------------------------


def test_an_edge_naming_a_node_that_does_not_exist_is_refused_at_construction():
    """Otherwise it surfaces as a silent early stop, hours later, on a task
    nobody is watching."""
    with pytest.raises(ValueError, match="typo|not a node"):
        DAG(
            name="broken",
            nodes=(node("a"),),
            edges=(Edge("a", "typo"),),
        )


def test_duplicate_node_names_are_refused():
    with pytest.raises(ValueError, match="duplicate"):
        DAG(name="dup", nodes=(node("a"), node("a")))


def test_a_dag_with_no_nodes_is_refused():
    with pytest.raises(ValueError, match="no nodes"):
        DAG(name="empty", nodes=())


def test_entry_defaults_to_the_first_node():
    dag = DAG(name="d", nodes=(node("first"), node("second")))
    assert dag.entry == "first"


# --- running ---------------------------------------------------------------


async def test_a_linear_dag_runs_every_node_in_order():
    ran: list[str] = []
    dag = DAG(
        name="linear",
        nodes=(node("a", record=ran), node("b", record=ran), node("c", record=ran)),
        edges=(Edge("a", "b"), Edge("b", "c")),
    )

    state = await DAGRunner(dag).run()

    assert ran == ["a", "b", "c"]
    assert state["c"] == "c-ran"


async def test_a_conditional_edge_skips_the_branch_it_does_not_take():
    ran: list[str] = []
    dag = DAG(
        name="branching",
        nodes=(
            node("check", produces={"actionable": False}, record=ran),
            node("fix", record=ran),
            node("reply", record=ran),
        ),
        edges=(
            Edge("check", "fix", when=lambda s: s["check"]["actionable"]),
            Edge("check", "reply", when=lambda s: not s["check"]["actionable"]),
            Edge("fix", "reply"),
        ),
    )

    await DAGRunner(dag).run()

    assert ran == ["check", "reply"]
    assert "fix" not in ran


async def test_the_branch_is_taken_when_the_condition_holds():
    ran: list[str] = []
    dag = DAG(
        name="branching",
        nodes=(
            node("check", produces={"actionable": True}, record=ran),
            node("fix", record=ran),
            node("reply", record=ran),
        ),
        edges=(
            Edge("check", "fix", when=lambda s: s["check"]["actionable"]),
            Edge("check", "reply", when=lambda s: not s["check"]["actionable"]),
            Edge("fix", "reply"),
        ),
    )

    await DAGRunner(dag).run()

    assert ran == ["check", "fix", "reply"]


# --- resume ----------------------------------------------------------------


async def test_a_node_with_a_recorded_result_does_not_run_again():
    """The whole point of the checkpoint. Against a log store, re-running is
    minutes and real money."""
    ran: list[str] = []
    dag = DAG(
        name="resume",
        nodes=(node("a", record=ran), node("b", record=ran), node("c", record=ran)),
        edges=(Edge("a", "b"), Edge("b", "c")),
    )
    # Pretend a and b already ran in an earlier process.
    partial = DAGState.empty().with_result("a", "a-ran").with_result("b", "b-ran")

    state = await DAGRunner(dag, state=partial).run()

    assert ran == ["c"]
    assert state["a"] == "a-ran"  # preserved, not recomputed


async def test_resuming_a_finished_dag_runs_nothing():
    ran: list[str] = []
    dag = DAG(name="done", nodes=(node("a", record=ran),))
    finished = DAGState.empty().with_result("a", "a-ran")

    await DAGRunner(dag, state=finished).run()

    assert ran == []


async def test_the_state_is_checkpointed_after_every_node_not_at_the_end():
    """A checkpoint written only at the end would resume from nothing — the
    exact failure the table exists to prevent."""
    saved: list[dict] = []

    async def on_checkpoint(state: DAGState) -> None:
        saved.append(state.to_dict())

    dag = DAG(
        name="checkpointing",
        nodes=(node("a"), node("b"), node("c")),
        edges=(Edge("a", "b"), Edge("b", "c")),
    )

    await DAGRunner(dag, on_checkpoint=on_checkpoint).run()

    assert [sorted(s) for s in saved] == [["a"], ["a", "b"], ["a", "b", "c"]]


async def test_a_failing_checkpoint_does_not_lose_the_run():
    """Losing the ability to resume is bad; losing the work in flight is
    worse. The run continues and says so in the log."""

    async def explode(_: DAGState) -> None:
        raise RuntimeError("disk full")

    ran: list[str] = []
    dag = DAG(name="d", nodes=(node("a", record=ran), node("b", record=ran)),
              edges=(Edge("a", "b"),))

    state = await DAGRunner(dag, on_checkpoint=explode).run()

    assert ran == ["a", "b"]
    assert state["b"] == "b-ran"


# --- pause -----------------------------------------------------------------


async def test_a_node_that_cannot_decide_raises_rather_than_guessing():
    async def unsure(state: DAGState, deps: DAGDeps):
        raise PauseForHuman(
            question="Which environment did this run against?",
            options=["production", "staging"],
            evidence={"log_lines": 3},
        )

    dag = DAG(name="asks", nodes=(Node("unsure", unsure),))

    with pytest.raises(PauseForHuman) as caught:
        await DAGRunner(dag).run()

    assert "Which environment" in str(caught.value)
    assert caught.value.options == ["production", "staging"]
    assert caught.value.evidence == {"log_lines": 3}


async def test_a_pause_keeps_the_nodes_that_already_finished():
    """Resume after the operator answers must not repeat the expensive part."""

    async def unsure(state: DAGState, deps: DAGDeps):
        raise PauseForHuman(question="which one?")

    dag = DAG(
        name="pauses",
        nodes=(node("expensive"), Node("unsure", unsure)),
        edges=(Edge("expensive", "unsure"),),
    )
    runner = DAGRunner(dag)

    with pytest.raises(PauseForHuman):
        await runner.run()

    assert runner.state["expensive"] == "expensive-ran"


def test_a_pause_with_options_renders_them_for_the_operator():
    pause = PauseForHuman(question="Apply the fix?", options=["yes", "no"])
    assert str(pause) == "Apply the fix? (yes / no)"


def test_a_pause_without_options_is_just_the_question():
    assert str(PauseForHuman(question="What now?")) == "What now?"


# --- cycles ----------------------------------------------------------------


async def test_a_cycle_in_the_edges_stops_instead_of_spinning():
    dag = DAG(
        name="cyclic",
        nodes=(node("a"), node("b")),
        edges=(Edge("a", "b"), Edge("b", "a")),
    )

    # a and b both complete on the first pass; the cycle then walks between
    # two finished nodes forever without the step guard.
    with pytest.raises(RuntimeError, match="cycle"):
        await DAGRunner(dag, max_steps=5).run()


# --- router ----------------------------------------------------------------


def test_registering_a_dag_makes_it_findable_by_task_type():
    dag = DAG(name="test_dag", nodes=(node("a"),))
    register_dag("test_type_32", dag)
    try:
        assert dag_for("test_type_32") is dag
    finally:
        EDGE_ROUTER.pop("test_type_32", None)


def test_two_dags_claiming_one_task_type_is_refused():
    """The second registration silently winning is the kind of mistake that
    surfaces as 'why is it running the old graph?' a week later."""
    first = DAG(name="first", nodes=(node("a"),))
    second = DAG(name="second", nodes=(node("a"),))
    register_dag("test_dup_32", first)
    try:
        with pytest.raises(ValueError, match="already registered"):
            register_dag("test_dup_32", second)
    finally:
        EDGE_ROUTER.pop("test_dup_32", None)


def test_a_task_type_with_no_dag_is_not_an_error():
    """The deterministic planner still handles it. None means 'use the
    simple path', not 'something is wrong'."""
    assert dag_for("no_such_type_32") is None


# --- integration with WorkflowRunner ---------------------------------------


async def test_a_registered_dag_runs_instead_of_the_planner(db):
    """The route ticket 32 buys: a task type with a DAG goes to the graph,
    and the graph's Action is what the runner acts on."""
    from friday.workflows import Reply
    from friday.workflows.runner import WorkflowRunner
    from tests.test_workflow_runner import make_task

    async def answers(state: DAGState, deps: DAGDeps):
        return Reply("traced it: the upstream timed out")

    register_dag("api_issue", DAG(name="api_issue_test", nodes=(Node("answer", answers),)))
    try:
        await make_task(db, correlation_id="abcdef01-2345-6789-abcd-ef0123456789")

        await WorkflowRunner(db=db, auto_ask=True).run_once()

        reply = [r for r in await db.outbound() if r.kind == "reply"]
        assert reply, "the DAG's Reply never reached the outbox"
        assert "upstream timed out" in reply[0].text
    finally:
        EDGE_ROUTER.pop("api_issue", None)


async def test_the_dag_state_is_persisted_between_passes(db):
    """Checkpoint through the real store: the second pass must not re-run a
    node the first pass finished."""
    from friday.workflows import Park
    from friday.workflows.runner import WorkflowRunner
    from tests.test_workflow_runner import make_task

    ran: list[str] = []

    async def expensive(state: DAGState, deps: DAGDeps):
        ran.append("expensive")
        return "log lines"

    async def decide(state: DAGState, deps: DAGDeps):
        ran.append("decide")
        return Park("has enough to trace")

    register_dag(
        "api_issue",
        DAG(
            name="two_step",
            nodes=(Node("expensive", expensive), Node("decide", decide)),
            edges=(Edge("expensive", "decide"),),
        ),
    )
    try:
        task = await make_task(db, correlation_id="abcdef01-2345-6789-abcd-ef0123456789")

        await WorkflowRunner(db=db, auto_ask=True).run_once()

        stored = await db.load_dag_state(task.id)
        assert stored["expensive"] == "log lines"
        # A Park does not survive JSON; the marker records that the node ran,
        # which is all a resume needs to know.
        assert stored["decide"] == {"__unstorable__": "Park"}
        assert ran == ["expensive", "decide"]
    finally:
        EDGE_ROUTER.pop("api_issue", None)


async def test_a_pause_parks_the_task_with_the_question(db):
    """PauseForHuman is the fourth outcome: not a guess, not a traceback, a
    question the operator can answer."""
    from friday.workflows.runner import WorkflowRunner
    from tests.test_workflow_runner import make_task

    async def unsure(state: DAGState, deps: DAGDeps):
        raise PauseForHuman(
            question="The fix touches a migration. Apply it?",
            options=["apply", "leave it"],
        )

    register_dag("api_issue", DAG(name="pausing", nodes=(Node("unsure", unsure),)))
    try:
        task = await make_task(db, correlation_id="abcdef01-2345-6789-abcd-ef0123456789")

        await WorkflowRunner(db=db, auto_ask=True).run_once()

        pause = await db.dag_pause(task.id)
        assert pause is not None
        _, question = pause
        assert "migration" in question

        told = [r for r in await db.outbound() if r.kind == "help_wanted"]
        assert told, "the operator was never told about the pause"
    finally:
        EDGE_ROUTER.pop("api_issue", None)


async def test_a_dag_that_finishes_without_an_action_parks_rather_than_inventing(db):
    """A graph that returns a string has not said what to send. Parking is
    honest; turning the value into a reply is not."""
    from friday.workflows.runner import WorkflowRunner
    from tests.test_workflow_runner import make_task

    async def shrugs(state: DAGState, deps: DAGDeps):
        return "some notes nobody asked to send"

    register_dag("api_issue", DAG(name="undecided", nodes=(Node("shrugs", shrugs),)))
    try:
        await make_task(db, correlation_id="abcdef01-2345-6789-abcd-ef0123456789")

        await WorkflowRunner(db=db, auto_ask=True).run_once()

        sent = [r for r in await db.outbound() if r.kind == "reply"]
        assert sent == [], "a value the graph never meant as a reply was sent"
    finally:
        EDGE_ROUTER.pop("api_issue", None)
