"""How a planner is registered.

The reflection runs once, at import — which is Starlette's and FastAPI's
answer: Starlette decides sync-versus-async when a route is registered rather
than per request, and FastAPI resolves a signature in the route's `__init__`,
so a bad one fails at import instead of at three in the morning.
"""

from __future__ import annotations

import pytest

from friday.models import ApiIssueParams, DocQuestionParams
from friday.workflows import Park, Reply, plan, planner

BROKEN = ApiIssueParams(summary="s")
TRACEABLE = ApiIssueParams(summary="s", correlation_id="abc-123")


class StubHarness:
    def __init__(self, answer=None):
        self.answer = answer

    async def run(self, prompt, **kw):
        from types import SimpleNamespace

        return SimpleNamespace(final_output=self.answer) if self.answer else None


async def test_a_deterministic_planner_declares_no_collaborators():
    """No lambda, no unused argument, no coroutine it did not need to be."""
    registry = {}

    @planner("api_issue", into=registry)
    def looks_at_the_parameters(params: ApiIssueParams):
        return Park("has enough to trace")

    assert isinstance(await plan("api_issue", TRACEABLE, planners=registry), Park)


async def test_a_planner_is_handed_only_what_it_names():
    registry = {}

    @planner("api_issue", into=registry)
    async def looks_it_up(params: ApiIssueParams, agent):
        answer = await agent.run("explain")
        return Reply(answer.final_output)

    action = await plan(
        "api_issue", TRACEABLE, agent=StubHarness("upstream timed out"),
        planners=registry,
    )

    assert action == Reply("upstream timed out")


async def test_the_function_is_left_alone_so_it_stays_testable():
    """Registering must not turn a pure function into something that needs a
    harness to call."""
    registry = {}

    @planner("api_issue", into=registry)
    def pure(params: ApiIssueParams):
        return Park("called directly")

    assert pure(BROKEN) == Park("called directly")


def test_a_task_type_that_does_not_exist_fails_at_import():
    """Silent before: the typo fell through to the generic rule and nobody
    could see why their planner never ran."""
    with pytest.raises(KeyError, match="api_isue"):

        @planner("api_isue", into={})
        def typo(params):
            return Park("never reached")


def test_asking_for_something_that_cannot_be_supplied_fails_at_import():
    with pytest.raises(TypeError, match="database"):

        @planner("doc_question", into={})
        def wants_too_much(params: DocQuestionParams, database):
            return Park("never reached")


async def test_a_registered_planner_is_found_without_being_passed_around():
    """The point of registering: the runner does not have to be told."""
    assert isinstance(await plan("api_issue", TRACEABLE), Park)


async def test_an_override_accepts_the_same_shapes_as_registration():
    """Otherwise trying a step before registering it means writing it
    differently from how it will finally be written."""

    def deterministic(params):
        return Park("sync, no collaborators")

    async def agentic(params, agent):
        return Reply(await agent.run("x") and "used the agent")

    assert await plan("api_issue", BROKEN, planners={"api_issue": deterministic})
    assert await plan(
        "api_issue", BROKEN, agent=StubHarness("y"),
        planners={"api_issue": agentic},
    ) == Reply("used the agent")
