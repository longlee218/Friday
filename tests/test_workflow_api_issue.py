"""The api_issue workflow — deterministic branching, no model.

The rule this encodes is the most frequent real action there is: a report
arrives without the fields needed to trace it, and the first move is to ask.
"""

from __future__ import annotations

from friday.triage import ApiIssueParams
from friday.workflows import Ask, Park, plan_api_issue


def params(**kw):
    return ApiIssueParams(summary="checkout is 500", **kw)


def test_a_report_with_nothing_to_trace_on_asks_for_details():
    action = plan_api_issue(params())

    assert isinstance(action, Ask)
    assert "correlationId" in action.text or "curl" in action.text.lower()


def test_a_correlation_id_is_enough_to_stop_asking():
    assert isinstance(plan_api_issue(params(correlation_id="7f3a91c2")), Park)


def test_a_curl_is_enough_to_stop_asking():
    assert isinstance(plan_api_issue(params(curl="curl https://x")), Park)


def test_the_environment_is_asked_for_only_when_it_is_missing():
    without = plan_api_issue(params())
    with_env = plan_api_issue(params(environment="production"))

    assert "environment" in without.text.lower()
    assert isinstance(with_env, Ask)
    assert "environment" not in with_env.text.lower()


def test_an_environment_alone_is_not_enough_to_trace():
    """You cannot find a request from the environment name."""
    assert isinstance(plan_api_issue(params(environment="production")), Ask)
