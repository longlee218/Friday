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


def test_a_node_marked_unstorable_runs_again_on_resume():
    """Skipping it would leave a hole every downstream node reads as absence.
    Re-running is the lesser cost, and the fix where it matters is to split
    the node so the expensive half returns plain data."""

    class NotJson:
        pass

    restored = DAGState.from_dict(
        DAGState.empty().with_result("expensive", NotJson()).to_dict()
    )

    assert not restored.has("expensive")


def test_the_stored_row_keeps_the_marker_even_though_resume_drops_it():
    """The two directions are asymmetric on purpose: the row is also a record
    of what happened, and the marker is the only trace a non-storable result
    leaves behind."""

    class NotJson:
        pass

    stored = DAGState.empty().with_result("a", NotJson()).to_dict()

    assert stored["a"] == {UNSTORABLE: "NotJson"}


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

    EDGE_ROUTER.pop("api_issue", None)
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

    EDGE_ROUTER.pop("api_issue", None)
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

    EDGE_ROUTER.pop("api_issue", None)
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

    EDGE_ROUTER.pop("api_issue", None)
    register_dag("api_issue", DAG(name="undecided", nodes=(Node("shrugs", shrugs),)))
    try:
        await make_task(db, correlation_id="abcdef01-2345-6789-abcd-ef0123456789")

        await WorkflowRunner(db=db, auto_ask=True).run_once()

        sent = [r for r in await db.outbound() if r.kind == "reply"]
        assert sent == [], "a value the graph never meant as a reply was sent"
    finally:
        EDGE_ROUTER.pop("api_issue", None)


# --- what the graph decided, vs what was declared last ---------------------


async def test_a_bookkeeping_node_declared_last_does_not_discard_the_reply():
    """Declaration order is not execution order. A graph that ends with an
    audit line or a cleanup still decided something, and answering from the
    node tuple instead of the path throws that decision away — then parks
    with a reason that is not true."""
    from friday.workflows import Reply
    from friday.workflows.runner import WorkflowRunner

    async def compose(s, d):
        return Reply("the real answer")

    async def audit(s, d):
        return "audit note, not an action"

    dag = DAG(
        name="t",
        nodes=(Node("compose", compose), Node("audit", audit)),
        edges=(Edge("compose", "audit"),),
    )
    runner = DAGRunner(dag)
    final = await runner.run()

    assert WorkflowRunner._outcome(dag, final, runner.trail) == Reply(
        "the real answer"
    )


async def test_a_node_returning_none_after_the_decision_does_not_discard_it():
    """`None` means "nothing to record", which the node docstring encourages.
    It must not also mean "forget what the graph decided"."""
    from friday.workflows import Reply
    from friday.workflows.runner import WorkflowRunner

    async def compose(s, d):
        return Reply("answer")

    async def cleanup(s, d):
        return None

    dag = DAG(
        name="t",
        nodes=(Node("compose", compose), Node("cleanup", cleanup)),
        edges=(Edge("compose", "cleanup"),),
    )
    runner = DAGRunner(dag)
    final = await runner.run()

    assert WorkflowRunner._outcome(dag, final, runner.trail) == Reply("answer")


async def test_the_trail_records_resumed_nodes_too():
    """A resumed run must be able to answer "what did this decide?" the same
    way a fresh one does, even though it re-ran nothing."""
    from friday.workflows import Reply
    from friday.workflows.runner import WorkflowRunner

    async def compose(s, d):
        return Reply("answer")

    dag = DAG(name="t", nodes=(Node("compose", compose),))
    finished = DAGState.empty().with_result("compose", Reply("answer"))

    runner = DAGRunner(dag, state=finished)
    final = await runner.run()

    assert runner.trail == ["compose"]
    assert WorkflowRunner._outcome(dag, final, runner.trail) == Reply("answer")


async def test_a_conditional_edge_reading_a_dropped_marker_reruns_the_node(db):
    """The node whose result did not survive storage runs again, so the
    predicate reads a real value rather than a marker it cannot index."""

    class Verdict:
        def __getitem__(self, key):
            return True

    ran: list[str] = []

    async def check(s, d):
        ran.append("check")
        return Verdict()

    async def fix(s, d):
        ran.append("fix")
        return "fixed"

    dag = DAG(
        name="markers",
        nodes=(Node("check", check), Node("fix", fix)),
        edges=(Edge("check", "fix", when=lambda s: s["check"]["actionable"]),),
    )

    first = DAGRunner(dag)
    await first.run()
    stored = first.state.to_dict()
    assert stored["check"] == {UNSTORABLE: "Verdict"}

    ran.clear()
    await DAGRunner(dag, state=DAGState.from_dict(stored)).run()

    assert ran == ["check"], "the predicate could not read a marker"


async def test_state_from_a_different_graph_is_not_applied(db):
    """Node names only mean something inside the graph that defined them.
    Handing a rewritten graph its predecessor's results makes it skip nodes on
    the strength of work that was never done."""
    await db.save_dag_state(
        1,
        dag_name="the_old_graph",
        results={"read_logs": "stale lines"},
        params_fingerprint="same-inputs",
    )

    assert await db.load_dag_state(1, dag_name="the_new_graph") is None
    assert await db.load_dag_state(1, dag_name="the_old_graph") == {
        "read_logs": "stale lines"
    }
    # No name asked, no opinion offered — the raw row comes back.
    assert await db.load_dag_state(1) == {"read_logs": "stale lines"}


# --- state is only good for the inputs that produced it --------------------


async def test_answering_the_question_re_runs_the_nodes_that_asked_it(db):
    """The regression this guards against is the whole point of asking.

    The graph runs, finds no correlationId, and every node concludes "nothing
    to look up" — a legitimate result, checkpointed like any other. The
    reporter then supplies the id. Without a check on the inputs the runner
    reads that state back, sees `read_logs` is done, and parks having read no
    logs at all: the system asked a question, got the answer, and ignored it.
    """
    from friday.workflows import Ask, Park
    from friday.workflows.runner import WorkflowRunner
    from tests.test_workflow_runner import make_task

    looked_up: list[str | None] = []

    async def read_logs(state: DAGState, deps: DAGDeps):
        given = deps.task.params.get("correlation_id")
        looked_up.append(given)
        return "500 at checkout" if given else None

    async def decide(state: DAGState, deps: DAGDeps):
        return Park("traced") if state.get("read_logs") else Ask("correlationId?")

    EDGE_ROUTER.pop("api_issue", None)
    register_dag(
        "api_issue",
        DAG(
            name="trace",
            nodes=(Node("read_logs", read_logs), Node("decide", decide)),
            edges=(Edge("read_logs", "decide"),),
        ),
    )
    try:
        task = await make_task(db)
        await WorkflowRunner(db=db, auto_ask=True).run_once()
        assert looked_up == [None]

        # They answered. The task goes back to pending with the id filled in.
        await db.set_task_params(
            task.id, {**task.params, "correlation_id": "abcdef01-2345-6789-abcd-ef0123456789"}
        )
        await db.move_task(task.id, "pending")

        await WorkflowRunner(db=db, auto_ask=True).run_once()
    finally:
        EDGE_ROUTER.pop("api_issue", None)

    assert looked_up == [None, "abcdef01-2345-6789-abcd-ef0123456789"], (
        "the second pass reused conclusions drawn without the correlationId"
    )


async def test_state_survives_a_pass_that_changed_nothing(db):
    """The other half: unchanged parameters must not throw the work away.

    A fingerprint that discarded state on every pass would be safe and
    useless — every resume would re-run every node and the checkpoint would
    buy nothing.
    """
    from friday.workflows import Park
    from friday.workflows.runner import WorkflowRunner
    from tests.test_workflow_runner import make_task

    ran: list[str] = []

    async def expensive(state: DAGState, deps: DAGDeps):
        ran.append("expensive")
        return "log lines"

    async def decide(state: DAGState, deps: DAGDeps):
        return Park("traced")

    EDGE_ROUTER.pop("api_issue", None)
    register_dag(
        "api_issue",
        DAG(
            name="stable",
            nodes=(Node("expensive", expensive), Node("decide", decide)),
            edges=(Edge("expensive", "decide"),),
        ),
    )
    try:
        task = await make_task(db, correlation_id="abcdef01-2345-6789-abcd-ef0123456789")
        await WorkflowRunner(db=db, auto_ask=True).run_once()
        await db.move_task(task.id, "pending")
        await WorkflowRunner(db=db, auto_ask=True).run_once()
    finally:
        EDGE_ROUTER.pop("api_issue", None)

    assert ran == ["expensive"], "unchanged parameters re-ran a finished node"


async def test_a_pause_records_the_node_that_paused_not_the_graph(db):
    """`paused_at_node` is read by a human deciding where to look. Storing
    the graph's name there answers a question nobody asked."""
    from friday.workflows.runner import WorkflowRunner
    from tests.test_workflow_runner import make_task

    async def fine(state: DAGState, deps: DAGDeps):
        return "ok"

    async def stops(state: DAGState, deps: DAGDeps):
        raise PauseForHuman("this needs a migration")

    EDGE_ROUTER.pop("api_issue", None)
    register_dag(
        "api_issue",
        DAG(
            name="pauses",
            nodes=(Node("fine", fine), Node("fix_bug", stops)),
            edges=(Edge("fine", "fix_bug"),),
        ),
    )
    try:
        task = await make_task(db, correlation_id="abcdef01-2345-6789-abcd-ef0123456789")
        await WorkflowRunner(db=db, auto_ask=True).run_once()
        assert await db.dag_pause(task.id) == ("fix_bug", "this needs a migration")
    finally:
        EDGE_ROUTER.pop("api_issue", None)


async def test_the_question_a_graph_paused_on_reaches_the_operator(db):
    """A pause asks something specific. The message that tells the operator
    there is work must carry it, or they open the board to find out what the
    system already knew."""
    from friday.workflows.runner import WorkflowRunner
    from tests.test_workflow_runner import make_task

    async def stops(state: DAGState, deps: DAGDeps):
        raise PauseForHuman("the cause mentions a migration, I have not touched it")

    EDGE_ROUTER.pop("api_issue", None)
    register_dag("api_issue", DAG(name="pauses", nodes=(Node("fix_bug", stops),)))
    try:
        await make_task(db, correlation_id="abcdef01-2345-6789-abcd-ef0123456789")
        await WorkflowRunner(db=db, auto_ask=True).run_once()
    finally:
        EDGE_ROUTER.pop("api_issue", None)

    (told,) = [r for r in await db.outbound() if r.kind == "help_wanted"]
    assert "migration" in told.text
    assert "fix_bug" in told.text


def test_the_composition_root_registers_the_graphs():
    """The graphs only exist because startup asks for them. Nothing else
    imports `register_dags`, so if this call goes the router stays empty, no
    test notices, and every api_issue task quietly takes the deterministic
    path again."""
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "run_agent.py"
    assert "register_dags(" in source.read_text()


async def test_a_second_pause_asks_a_second_question(db):
    """The regression the fingerprint work created the conditions for.

    The graph pauses, the operator is told. They answer, the parameters
    change, the state is discarded, the graph re-runs — and pauses on
    something else. Keyed on the fact of an announcement rather than on what
    it said, that second question would be swallowed by the first one's row
    and the task would sit in NEEDS_HUMAN with nobody told.
    """
    from friday.workflows.runner import WorkflowRunner
    from tests.test_workflow_runner import make_task

    asked: list[str] = []

    async def stops(state: DAGState, deps: DAGDeps):
        question = (
            "which environment?"
            if not deps.task.params.get("environment")
            else "is this the production database or the replica?"
        )
        asked.append(question)
        raise PauseForHuman(question, node="triage_it")

    EDGE_ROUTER.pop("api_issue", None)
    register_dag("api_issue", DAG(name="asks", nodes=(Node("triage_it", stops),)))
    try:
        task = await make_task(db, correlation_id="abcdef01-2345-6789-abcd-ef0123456789")
        await WorkflowRunner(db=db, auto_ask=True).run_once()

        await db.set_task_params(task.id, {**task.params, "environment": "production"})
        await db.move_task(task.id, "pending")
        await WorkflowRunner(db=db, auto_ask=True).run_once()
    finally:
        EDGE_ROUTER.pop("api_issue", None)

    told = [r.text for r in await db.outbound() if r.kind == "help_wanted"]
    assert len(told) == 2, f"the second question never went out; asked {asked}"
    assert "which environment" in told[0]
    assert "replica" in told[1]


async def test_the_same_question_is_not_asked_twice(db):
    """The other half. A notification that repeats is one you learn to
    ignore, and a task sitting untouched has nothing new to say."""
    from friday.workflows.runner import WorkflowRunner
    from tests.test_workflow_runner import make_task

    async def stops(state: DAGState, deps: DAGDeps):
        raise PauseForHuman("which environment?", node="triage_it")

    EDGE_ROUTER.pop("api_issue", None)
    register_dag("api_issue", DAG(name="asks", nodes=(Node("triage_it", stops),)))
    try:
        task = await make_task(db, correlation_id="abcdef01-2345-6789-abcd-ef0123456789")
        await WorkflowRunner(db=db, auto_ask=True).run_once()
        await db.move_task(task.id, "pending")
        await WorkflowRunner(db=db, auto_ask=True).run_once()
        await WorkflowRunner(db=db, auto_ask=True).run_once()
    finally:
        EDGE_ROUTER.pop("api_issue", None)

    told = [r.text for r in await db.outbound() if r.kind == "help_wanted"]
    assert len(told) == 1


async def test_a_malformed_value_is_challenged_before_the_graph_runs(db):
    """The graph route used to skip extraction and validation entirely, so
    `api_issue` — the only type with both an extractor and rules — got
    neither. A correlationId of "not-a-uuid" reached the graph, looked
    findable, and parked to the operator instead of asking the reporter to
    send a real one."""
    from friday.workflows.runner import WorkflowRunner
    from tests.test_workflow_runner import make_task

    ran: list[str] = []

    async def investigate(state: DAGState, deps: DAGDeps):
        ran.append("investigate")
        return None

    EDGE_ROUTER.pop("api_issue", None)
    register_dag(
        "api_issue", DAG(name="graph", nodes=(Node("investigate", investigate),))
    )
    try:
        await make_task(db, correlation_id="not-a-uuid")
        await WorkflowRunner(db=db, auto_ask=True).run_once()
    finally:
        EDGE_ROUTER.pop("api_issue", None)

    assert ran == [], "the graph ran on a value validation should have caught"
    (asked,) = [r for r in await db.outbound() if r.kind == "ask_for_details"]
    assert "uuid" in asked.text


def test_the_fingerprint_does_not_confuse_a_separator_for_a_field_boundary():
    """Joining `key=value` made `{"a": "b=c"}` and `{"a=b": "c"}` the same
    fingerprint, and `1` the same as `"1"`. Unreachable with today's `str |
    None` fields, but this is handed the raw JSON-decoded dict, so the type
    discipline it relied on is not enforced at its own edge."""
    from friday.workflows.runner import _fingerprint

    assert _fingerprint({"a": "b=c"}) != _fingerprint({"a=b": "c"})
    assert _fingerprint({"correlation_id": 1}) != _fingerprint({"correlation_id": "1"})


def test_an_absent_field_and_an_empty_one_are_the_same_absence():
    """`""` and `None` and not-there mean the same for a `str | None` field.
    Discarding a graph's work over that distinction costs tool calls for
    nothing."""
    from friday.workflows.runner import _fingerprint

    assert _fingerprint({"a": "x", "b": ""}) == _fingerprint({"a": "x"})
    assert _fingerprint({"a": "x", "b": None}) == _fingerprint({"a": "x"})
    # And no-parameters-at-all is still a real digest, never the empty string
    # that `save_dag_state` refuses.
    assert _fingerprint({})


async def test_a_pause_computed_against_other_parameters_is_not_announced(db):
    """A pause is cleared by a checkpoint, and a checkpoint only happens after
    a node completes — so a run that discards its state and then fails leaves
    the old question sitting there. Announcing it asks the reporter the very
    thing they just answered, with their answer visible in the same message."""
    from friday.workflows.runner import WorkflowRunner, _fingerprint
    from tests.test_workflow_runner import make_task

    task = await make_task(db)
    await db.save_dag_state(
        task.id,
        dag_name="api_issue",
        results={},
        params_fingerprint=_fingerprint({"summary": "something else entirely"}),
        paused_at_node="first",
        paused_question="Which environment is this?",
    )

    current = {task.id: _fingerprint(task.params)}
    assert await db.dag_pauses(current) == {}


def test_a_value_that_changes_shape_in_storage_is_marked_not_stored():
    """`json.dumps` succeeds on a tuple and on integer dict keys, and they
    come back as a list and as string keys. A node checking
    `isinstance(x, tuple)` then works until the first restart and fails after
    it, with nothing in between to say why — which is the class of bug the
    marker exists to prevent, arriving through a value that serialises."""
    stored = (
        DAGState.empty()
        .with_result("tupled", ("x", "y"))
        .with_result("int_keys", {1: "v"})
        .with_result("plain", {"ok": [1, 2]})
        .to_dict()
    )

    assert stored["tupled"] == {UNSTORABLE: "tuple"}
    assert stored["int_keys"] == {UNSTORABLE: "dict"}
    assert stored["plain"] == {"ok": [1, 2]}
    # And the marked ones run again rather than coming back a different shape.
    assert list(DAGState.from_dict(stored).results) == ["plain"]
