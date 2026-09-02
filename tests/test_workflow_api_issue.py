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

from friday.domain.actions import Ask, HandOver
from friday.workflows import prepare


async def _decide(task_type, params, *, text=None):
    """The simple path, exactly as `WorkflowRunner._plan` walks it.

    There was a `plan()` doing this, and every test here called it. Production
    did not — `_plan` calls these two and routes to a graph in between — so a
    convenience wrapper had become a second path that only tests took, which is
    how five tests came to hold an unreachable branch elsewhere in this file.
    """
    from friday.workflows import plan_by_required_parameters, prepare

    params, problem = await prepare(task_type, params, text=text)
    return problem or plan_by_required_parameters(task_type, params)


def params(**kw):
    return ApiIssueParams(summary="checkout is 500", **kw)


CID = "abcdef01-2345-6789-abcd-ef0123456789"


async def gate(**kw):
    """What the *route* decides — which is where this is decided.

    These used to drive `_compose_reply` directly and assert on the `Ask` it
    returned. That `Ask` is gone: the rule moved into `prepare()`, so a report
    with nothing to trace on never reaches a node at all. Asserting on the node
    was asserting on a path production stopped taking.
    """
    _, problem = await prepare("api_issue", params(**kw))
    return problem


async def decide(**kw):
    """What the graph replies with once it *has* run and found nothing."""
    task = SimpleNamespace(params={"summary": "checkout is 500", **kw})
    state = DAGState.empty().with_result("prepare", ApiIssueParams(**task.params))
    return await _compose_reply(state, DAGDeps(task=task))


async def test_a_report_with_nothing_to_trace_on_asks_for_details():
    action = await gate()

    assert isinstance(action, Ask)
    assert "correlationId" in action.text or "curl" in action.text.lower()


async def test_a_correlation_id_is_enough_to_reach_the_graph():
    assert await gate(correlation_id=CID) is None


async def test_a_curl_is_enough_to_reach_the_graph():
    assert await gate(curl="curl https://x") is None


async def test_an_environment_alone_is_not_enough_to_trace():
    """You cannot find a request from the environment name."""
    assert isinstance(await gate(environment="production"), Ask)


async def test_a_graph_that_found_nothing_hands_over_rather_than_asking_again():
    """By the time a node runs, the reporter has already given something to
    trace on — `_traceable` saw to that. So "nothing found" is the
    investigation coming up empty, which is a person's problem, not another
    question for the reporter."""
    action = await decide(correlation_id=CID)

    assert isinstance(action, HandOver)
    assert "nothing was found" in action.reason


# ---- the other task types --------------------------------------------------


async def test_an_access_request_without_a_project_asks_for_one():
    """Same gap as api_issue had: a task that cannot be acted on has to say so,
    not sit in a queue nobody is watching."""
    action = await _decide(
        "access_request", AccessRequestParams(project="", permission="write",
                                              summary="needs access")
    )

    assert isinstance(action, Ask)
    assert "project" in action.text


async def test_a_doc_question_without_a_question_asks_for_one():
    action = await _decide("doc_question", DocQuestionParams(question="", doc_ref=None))

    assert isinstance(action, Ask)


async def test_an_optional_parameter_is_never_asked_for():
    """`doc_ref` is optional by its type. Asking for it would be asking for
    something we said we did not need."""
    action = await _decide("doc_question", DocQuestionParams(question="how does X work?",
                                                    doc_ref=None))

    assert isinstance(action, HandOver)


async def test_a_complete_request_hands_over_because_nothing_can_act_on_it_yet():
    """Nothing grants access. Handing over is honest; asking again would not be."""
    action = await _decide(
        "access_request",
        AccessRequestParams(project="backend", permission="write", summary="s"),
    )

    assert isinstance(action, HandOver)


async def test_the_summary_is_never_asked_for():
    """The model writes it. Asking the reporter for a summary of their own
    message is nonsense."""
    action = await _decide(
        "access_request", AccessRequestParams(project="backend",
                                              permission="write", summary="")
    )

    assert isinstance(action, HandOver)


async def test_api_issue_keeps_its_own_rule():
    """A correlationId makes a request findable; its type cannot say that."""
    traceable = ApiIssueParams(
        summary="s",
        environment=None,
        correlation_id="abcdef01-2345-6789-abcd-ef0123456789",
        curl=None,
    )

    assert isinstance(await _decide("api_issue", traceable), HandOver)


async def test_a_malformed_correlation_id_is_caught_by_the_rule():
    """A value that does not look like a uuid is rejected before dispatch.

    Ticket 31 adds InSet/Matches rules to ApiIssueParams; ticket 30 wired
    validate into plan(). Together they catch what triage left through that
    the structural check did not: not 'field is missing', but 'field is
    wrong'."""
    bad = ApiIssueParams(summary="s", correlation_id="abc-123")

    action = await _decide("api_issue", bad)

    assert isinstance(action, Ask)
    assert "uuid" in action.text


# ---- ask_clarification: code stays the floor (D12) --------------------------


async def _prepare_with_clarify(params_obj, clarify, *, extracted=None, monkeypatch):
    """`prepare()` with `extract()` stood in for, so the ordering between
    code's own floor and a model's `Clarify` can be tested without a real
    extractor or model."""
    import friday.workflows as wf

    async def stub_extract(task_type, text):
        return extracted, clarify

    monkeypatch.setattr(wf, "_extract", stub_extract)
    return await wf.prepare("api_issue", params_obj, text="irrelevant")


async def test_code_floor_wins_over_a_clarify_that_names_a_different_field(monkeypatch):
    """The extractor asked about `environment`, but `correlation_id` is
    malformed — code's own rule is what the reporter is challenged with,
    because a value the rules reject cannot be waved through by the model
    having asked about something else instead."""
    from friday.extraction import Clarify

    bad = params(correlation_id="not-a-uuid")
    clarify = Clarify(fields=("environment",), because="no server named")

    _, action = await _prepare_with_clarify(bad, clarify, monkeypatch=monkeypatch)

    assert isinstance(action, Ask)
    assert "uuid" in action.text
    assert "server" not in action.text, "the model's own wording must not leak in here"


async def test_a_clarify_for_an_already_filled_field_is_not_honoured(monkeypatch):
    """The model asked about `correlation_id`, but it is already there — from
    the reporter, or from this same extraction run. Asking again for
    something already answered is not a question this exists to ask."""
    from friday.extraction import Clarify

    complete = params(correlation_id=CID)
    clarify = Clarify(fields=("correlation_id",), because="not sure")

    _, action = await _prepare_with_clarify(complete, clarify, monkeypatch=monkeypatch)

    assert action is None, "nothing left to ask about once the field is filled"


async def test_a_clarify_becomes_an_ask_once_code_has_nothing_to_say(monkeypatch):
    """A report with a curl is traceable — code's own rules find nothing
    wrong — but the extractor read something worth asking about anyway.
    That is the case `ask_clarification` exists for."""
    from friday.extraction import Clarify

    traceable = params(curl="curl -X GET /pay")
    clarify = Clarify(fields=("environment",), because="curl doesn't say which server")

    _, action = await _prepare_with_clarify(traceable, clarify, monkeypatch=monkeypatch)

    assert isinstance(action, Ask)
    assert "environment" in action.text.lower()


# ---- the end of the simple path --------------------------------------------


async def test_plan_by_required_parameters_hands_over_once_everything_is_present():
    """`plan_by_required_parameters` on its own, once `prepare` has left
    nothing outstanding: no question left to ask, and hand-over is what
    "a human takes it from here" looks like."""
    action = await _decide(
        "api_issue",
        ApiIssueParams(
            summary="s", correlation_id="abcdef01-2345-6789-abcd-ef0123456789"
        ),
    )

    assert isinstance(action, HandOver)


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
