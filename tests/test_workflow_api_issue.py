"""The api_issue workflow — deterministic branching, no model.

The rule this encodes is the most frequent real action there is: a report
arrives without the fields needed to trace it, and the first move is to ask.

Ticket 33 moved that rule from `plan_api_issue` into the last node of the
`api_issue` graph. These tests follow it there: they exercise
`_compose_reply` with no agents and no tool servers, which is exactly the
state a fresh install is in, and assert the same outcomes the planner gave.
"""

from __future__ import annotations

from friday.dag import DAGDeps, DAGState
from friday.dag.api_issue import _compose_reply
from friday.domain.models import AccessRequestParams, ApiIssueParams, DocQuestionParams
from types import SimpleNamespace

from friday.workflows import Ask, Park, plan


def params(**kw):
    return ApiIssueParams(summary="checkout is 500", **kw)


async def decide(**kw):
    """What the graph replies with when nothing could be investigated.

    `task.params` is a plain dict in the database, which is what the node
    reads, so the stand-in is a dict rather than a `Params` instance.
    """
    task = SimpleNamespace(params={"summary": "checkout is 500", **kw})
    return await _compose_reply(DAGState.empty(), DAGDeps(task=task))


async def test_a_report_with_nothing_to_trace_on_asks_for_details():
    action = await decide()

    assert isinstance(action, Ask)
    assert "correlationId" in action.text or "curl" in action.text.lower()


async def test_a_correlation_id_is_enough_to_stop_asking():
    assert isinstance(await decide(correlation_id="7f3a91c2"), Park)


async def test_a_curl_is_enough_to_stop_asking():
    assert isinstance(await decide(curl="curl https://x"), Park)


async def test_the_environment_is_asked_for_only_when_it_is_missing():
    without = await decide()
    with_env = await decide(environment="production")

    assert "environment" in without.text.lower()
    assert isinstance(with_env, Ask)
    assert "environment" not in with_env.text.lower()


async def test_an_environment_alone_is_not_enough_to_trace():
    """You cannot find a request from the environment name."""
    assert isinstance(await decide(environment="production"), Ask)


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
    traceable = ApiIssueParams(
        summary="s",
        environment=None,
        correlation_id="abcdef01-2345-6789-abcd-ef0123456789",
        curl=None,
    )

    assert isinstance(await plan("api_issue", traceable), Park)


async def test_a_malformed_correlation_id_is_caught_by_the_rule():
    """A value that does not look like a uuid is rejected before dispatch.

    Ticket 31 adds InSet/Matches rules to ApiIssueParams; ticket 30 wired
    validate into plan(). Together they catch what triage left through that
    the structural check did not: not 'field is missing', but 'field is
    wrong'."""
    bad = ApiIssueParams(summary="s", correlation_id="abc-123")

    action = await plan("api_issue", bad)

    assert isinstance(action, Ask)
    assert "uuid" in action.text


# ---- the end of the simple path --------------------------------------------


async def test_a_type_with_no_graph_parks_once_it_has_what_it_needs():
    """The end of the simple path. Nothing here reaches a model: with every
    required parameter present there is no question left to ask, and parking
    is what "a human takes it from here" looks like."""
    action = await plan(
        "api_issue",
        ApiIssueParams(
            summary="s", correlation_id="abcdef01-2345-6789-abcd-ef0123456789"
        ),
    )

    assert isinstance(action, Park)


# --- a later extraction fills blanks; it does not revise what it said --------


def test_a_second_extraction_does_not_reword_the_first():
    """This runs again on every follow-up, and a model asked the same question
    twice does not give the same answer. Letting the second run rewrite the
    first cost nineteen direct messages about one report, each carrying a
    differently worded summary — a reworded value is a *changed* value, so the
    graph discarded its work and the operator was told again."""
    from friday.workflows import _fill

    filled = _fill(
        ApiIssueParams(summary="checkout is 500ing", environment="production"),
        ApiIssueParams(summary="User reports the API is failing", environment="prod"),
    )

    assert filled.summary == "checkout is 500ing"
    assert filled.environment == "production"


def test_a_later_extraction_fills_what_is_still_blank():
    """Which is exactly what a follow-up supplying the correlationId is, and
    the only way it reaches the task now that triage does not lift it out."""
    from friday.workflows import _fill

    filled = _fill(
        ApiIssueParams(summary="checkout is 500ing"),
        ApiIssueParams(
            summary="ignored",
            correlation_id="abcdef01-2345-6789-abcd-ef0123456789",
            environment="production",
        ),
    )

    assert filled.correlation_id == "abcdef01-2345-6789-abcd-ef0123456789"
    assert filled.environment == "production"
    assert filled.summary == "checkout is 500ing"


def test_an_empty_string_counts_as_a_blank():
    """A field the model wrote as "" is not a value someone supplied."""
    from friday.workflows import _fill

    assert _fill(
        ApiIssueParams(summary="s", environment=""),
        ApiIssueParams(summary="s", environment="production"),
    ).environment == "production"
