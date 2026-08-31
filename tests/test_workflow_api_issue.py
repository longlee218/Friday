"""The api_issue workflow — deterministic branching, no model.

The rule this encodes is the most frequent real action there is: a report
arrives without the fields needed to trace it, and the first move is to ask.
"""

from __future__ import annotations

from friday.models import AccessRequestParams, ApiIssueParams, DocQuestionParams
from types import SimpleNamespace

from friday.workflows import Ask, Park, Reply, plan, plan_api_issue


def params(**kw):
    return ApiIssueParams(summary="checkout is 500", **kw)


async def test_a_report_with_nothing_to_trace_on_asks_for_details():
    action = plan_api_issue(params())

    assert isinstance(action, Ask)
    assert "correlationId" in action.text or "curl" in action.text.lower()


async def test_a_correlation_id_is_enough_to_stop_asking():
    assert isinstance(plan_api_issue(params(correlation_id="7f3a91c2")), Park)


async def test_a_curl_is_enough_to_stop_asking():
    assert isinstance(plan_api_issue(params(curl="curl https://x")), Park)


async def test_the_environment_is_asked_for_only_when_it_is_missing():
    without = plan_api_issue(params())
    with_env = plan_api_issue(params(environment="production"))

    assert "environment" in without.text.lower()
    assert isinstance(with_env, Ask)
    assert "environment" not in with_env.text.lower()


async def test_an_environment_alone_is_not_enough_to_trace():
    """You cannot find a request from the environment name."""
    assert isinstance(plan_api_issue(params(environment="production")), Ask)


# ---- the other task types --------------------------------------------------


async def test_an_access_request_without_a_project_asks_for_one():
    """Same gap as api_issue had: a task that cannot be acted on has to say so,
    not sit in a queue nobody is watching."""
    action = await plan(
        "access_request", AccessRequestParams(project="", permission="write",
                                              summary="needs access")
    )

    assert isinstance(action, Ask)
    assert "project" in action.text


async def test_a_doc_question_without_a_question_asks_for_one():
    action = await plan("doc_question", DocQuestionParams(question="", doc_ref=None))

    assert isinstance(action, Ask)


async def test_an_optional_parameter_is_never_asked_for():
    """`doc_ref` is optional by its type. Asking for it would be asking for
    something we said we did not need."""
    action = await plan("doc_question", DocQuestionParams(question="how does X work?",
                                                    doc_ref=None))

    assert isinstance(action, Park)


async def test_a_complete_request_parks_because_nothing_can_act_on_it_yet():
    """Nothing grants access. Parking is honest; asking again would not be."""
    action = await plan(
        "access_request",
        AccessRequestParams(project="backend", permission="write", summary="s"),
    )

    assert isinstance(action, Park)


async def test_the_summary_is_never_asked_for():
    """The model writes it. Asking the reporter for a summary of their own
    message is nonsense."""
    action = await plan(
        "access_request", AccessRequestParams(project="backend",
                                              permission="write", summary="")
    )

    assert isinstance(action, Park)


async def test_api_issue_keeps_its_own_rule():
    """A correlationId makes a request findable; its type cannot say that."""
    traceable = ApiIssueParams(summary="s", environment=None,
                               correlation_id="abc-123", curl=None)

    assert isinstance(await plan("api_issue", traceable), Park)


# ---- an agentic step -------------------------------------------------------


class StubHarness:
    """Stands in for a Harness. The seam is what a planner is handed."""

    def __init__(self, answer=None):
        self.answer = answer
        self.prompts: list[str] = []

    async def run(self, prompt, **kw):
        self.prompts.append(prompt)
        return SimpleNamespace(final_output=self.answer) if self.answer else None


async def test_a_planner_may_be_asynchronous_and_use_an_agent():
    """Fetching is deterministic; reading is judgement. A planner that needs the
    second gets a harness, and the deterministic ones stay pure functions."""
    agent = StubHarness(answer="the upstream timed out")

    async def looks_it_up(params, agent):
        result = await agent.run(f"explain {params.correlation_id}")
        return Reply(result.final_output)

    action = await plan(
        "api_issue",
        ApiIssueParams(summary="s", correlation_id="abc-123"),
        agent=agent,
        planners={"api_issue": looks_it_up},
    )

    assert action == Reply("the upstream timed out")
    assert "abc-123" in agent.prompts[0]


async def test_a_deterministic_planner_needs_no_agent_and_gets_none():
    action = await plan(
        "api_issue", ApiIssueParams(summary="s", correlation_id="abc-123")
    )

    assert isinstance(action, Park)


async def test_an_agentic_planner_that_cannot_answer_parks():
    """The harness hands back nothing when it fails. Parking is honest; an
    invented answer in the operator's name is not."""

    async def looks_it_up(params, agent):
        result = await agent.run("explain")
        return Park("nothing conclusive") if result is None else Reply(result.final_output)

    action = await plan(
        "api_issue",
        ApiIssueParams(summary="s", correlation_id="abc-123"),
        agent=StubHarness(answer=None),
        planners={"api_issue": looks_it_up},
    )

    assert isinstance(action, Park)
