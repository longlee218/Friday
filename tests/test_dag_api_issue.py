"""Ticket 33 — the api_issue graph, exercised end to end with stubs.

No model, no tool server. Each node is reached through the real graph and
the real runner; only the agents are stubs. That is the level the graph's
own behaviour lives at — which nodes run, in what order, and what the last
one decides.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from friday.dag import DAGDeps, DAGRunner, DAGState
from friday.dag.api_issue import build_api_issue_dag
from friday.dag.pause import PauseForHuman
from friday.workflows import Ask, Park, Reply

LOGS = "12:00:01 ERROR checkout.py:42 upstream timed out"
UUID = "abcdef01-2345-6789-abcd-ef0123456789"


class StubAgent:
    """Stands in for a Harness. Records what it was asked."""

    def __init__(self, answer: str | None):
        self._answer = answer
        self.prompts: list[str] = []

    async def run(self, prompt, **kw):
        self.prompts.append(prompt)
        if self._answer is None:
            return None
        return SimpleNamespace(final_output=self._answer)


def deps(*, agents=None, servers=None, **params) -> DAGDeps:
    task = SimpleNamespace(
        id=1,
        params={
            "summary": "checkout is 500",
            "environment": None,
            "correlation_id": None,
            "curl": None,
            **params,
        },
    )
    return DAGDeps(task=task, servers=servers or {}, extra=agents or {})


async def run(**kw) -> DAGState:
    return await DAGRunner(build_api_issue_dag(), deps=deps(**kw)).run()


# --- the degenerate path: nothing configured -------------------------------


async def test_with_no_tools_the_graph_still_reaches_a_decision():
    """A fresh install has no log server and no node agents. The graph must
    still say something — and say the same thing the planner it replaced
    said."""
    state = await run()

    assert state["read_logs"] is None
    assert state["find_code_path"] is None
    assert state["analyze_stack"]["actionable"] is False
    assert isinstance(state["compose_reply"], Ask)
    assert "correlationId" in state["compose_reply"].text


async def test_a_traceable_report_parks_when_nothing_can_investigate_it():
    state = await run(correlation_id=UUID)

    assert isinstance(state["compose_reply"], Park)
    assert "enough to trace" in state["compose_reply"].reason


async def test_no_model_is_called_when_there_is_nothing_to_analyse():
    """The graph exists to give us somewhere to skip the expensive step."""
    analyst = StubAgent('{"cause": "invented", "actionable": true}')

    await run(agents={"analyze_stack": analyst})

    assert analyst.prompts == []


# --- the investigating path -------------------------------------------------


def _investigating(*, actionable: bool, fix: str | None = "patched") -> dict:
    return {
        "read_logs": StubAgent(LOGS),
        "find_code_path": StubAgent("checkout.py:42"),
        "analyze_stack": StubAgent(
            '{"cause": "upstream timed out", "actionable": %s, "evidence": []}'
            % ("true" if actionable else "false")
        ),
        "fix_bug": StubAgent(fix),
        "compose_reply": StubAgent("Cache đầy, anh clear rồi nhé"),
    }


async def test_the_state_accumulates_as_the_graph_walks():
    state = await run(
        agents=_investigating(actionable=False),
        servers={"loki": object(), "source": object()},
        correlation_id=UUID,
    )

    assert state["read_logs"] == LOGS
    assert state["find_code_path"] == "checkout.py:42"
    assert state["analyze_stack"]["cause"] == "upstream timed out"
    assert isinstance(state["compose_reply"], Reply)


async def test_fix_bug_is_skipped_when_the_cause_is_not_actionable():
    """`actionable: false` is the analyst saying it is guessing. Spending a
    code change on a guess is the thing the edge exists to prevent."""
    agents = _investigating(actionable=False)

    state = await run(
        agents=agents,
        servers={"loki": object(), "source": object()},
        correlation_id=UUID,
    )

    assert not state.has("fix_bug")
    assert agents["fix_bug"].prompts == []


async def test_fix_bug_runs_when_the_cause_is_actionable():
    agents = _investigating(actionable=True)

    state = await run(
        agents=agents,
        servers={"loki": object(), "source": object()},
        correlation_id=UUID,
    )

    assert state["fix_bug"] == "patched"
    assert isinstance(state["compose_reply"], Reply)


async def test_the_reply_carries_both_the_cause_and_the_fix():
    agents = _investigating(actionable=True)

    await run(
        agents=agents,
        servers={"loki": object(), "source": object()},
        correlation_id=UUID,
    )

    written = agents["compose_reply"].prompts[0]
    assert "upstream timed out" in written
    assert "patched" in written


# --- refusing rather than guessing ------------------------------------------


@pytest.mark.parametrize(
    "cause",
    [
        "the migration dropped the column",
        "the schema is out of date",
        "the credential expired",
        "an old token is cached",
    ],
)
async def test_a_cause_that_touches_something_dangerous_pauses(cause):
    """Widening what "actionable" is allowed to mean is how an agent ends up
    editing a migration at three in the morning."""
    agents = _investigating(actionable=True)
    agents["analyze_stack"] = StubAgent(
        '{"cause": "%s", "actionable": true, "evidence": []}' % cause
    )

    with pytest.raises(PauseForHuman) as caught:
        await run(
            agents=agents,
            servers={"loki": object(), "source": object()},
            correlation_id=UUID,
        )

    assert caught.value.node == "fix_bug"
    assert cause in caught.value.question
    assert agents["fix_bug"].prompts == [], "it tried to patch anyway"


async def test_an_actionable_cause_with_no_source_server_pauses_rather_than_lying():
    """It found the cause but cannot change anything from here. Saying so is
    the honest move; claiming a fix would not be."""
    agents = _investigating(actionable=True)

    with pytest.raises(PauseForHuman) as caught:
        await run(
            agents=agents,
            servers={"loki": object()},  # no source server
            correlation_id=UUID,
        )

    assert "cannot change code" in caught.value.question


# --- resume -----------------------------------------------------------------


async def test_resuming_after_a_pause_does_not_reread_the_logs():
    """The whole reason for the checkpoint. Re-reading a log store is the
    expensive part, and the operator answering a question is not a reason to
    pay for it twice."""
    agents = _investigating(actionable=True)
    agents["analyze_stack"] = StubAgent(
        '{"cause": "the migration is bad", "actionable": true, "evidence": []}'
    )
    dag = build_api_issue_dag()
    d = deps(
        agents=agents,
        servers={"loki": object(), "source": object()},
        correlation_id=UUID,
    )

    first = DAGRunner(dag, deps=d)
    with pytest.raises(PauseForHuman):
        await first.run()

    reads_before = len(agents["read_logs"].prompts)
    assert reads_before == 1

    # The operator answered; the graph runs again from where it stopped.
    second = DAGRunner(dag, deps=d, state=first.state)
    with pytest.raises(PauseForHuman):
        await second.run()

    assert len(agents["read_logs"].prompts) == reads_before, "it read the logs again"


async def test_an_analyst_that_ignores_the_format_is_not_treated_as_certain():
    """A shape we did not ask for is not evidence of certainty."""
    agents = _investigating(actionable=True)
    agents["analyze_stack"] = StubAgent("I think the cache is full, probably")

    state = await run(
        agents=agents,
        servers={"loki": object(), "source": object()},
        correlation_id=UUID,
    )

    assert state["analyze_stack"]["actionable"] is False
    assert state["analyze_stack"]["cause"] == "I think the cache is full, probably"
    assert not state.has("fix_bug")
