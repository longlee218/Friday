"""Ticket 04 slice 2 — the triage agent.

Driven with the SDK's ScriptedModel: no test makes a network call. What is being
tested is our tool surface and how a tool call becomes a decision — not the
model's judgement, which only a live run can speak to.
"""

from __future__ import annotations

import pytest
from agents.models.interface import Model
from agents.testing import ScriptedModel, assistant_message, function_call

from conftest import make_event
from friday.config import AgentConfig
from friday.triage import Decided, NeedsHuman, Triage

CONFIG = AgentConfig(
    name="triage",
    api_key="k",
    base_url="https://example.invalid/v1",
    model="test-model",
    options={"confidence_threshold": 0.7},
)


def triage_with(*steps) -> Triage:
    return Triage(config=CONFIG, model=ScriptedModel(list(steps)))


async def decide(triage, text="the api is wrong", context=()):
    return await triage.decide(make_event(text=text), context=context)


async def test_an_api_problem_becomes_an_api_issue_with_its_parameters():
    triage = triage_with([
        function_call("create_api_issue_task", {
            "confidence": 0.9,
            "summary": "checkout returns 500",
            "environment": "production",
            "correlation_id": "7f3a91c2",
            "curl": None,
        }, call_id="1")
    ])

    outcome = await decide(triage)

    assert isinstance(outcome, Decided)
    assert outcome.type == "api_issue"
    assert outcome.confidence == 0.9
    assert outcome.params.environment == "production"
    assert outcome.params.correlation_id == "7f3a91c2"
    assert outcome.params.curl is None


async def test_a_permission_request_becomes_an_access_request():
    triage = triage_with([
        function_call("create_access_request_task", {
            "confidence": 0.95,
            "project": "payment-service",
            "permission": "write",
            "summary": "needs write access",
        }, call_id="1")
    ])

    outcome = await decide(triage)

    assert outcome.type == "access_request"
    assert (outcome.params.project, outcome.params.permission) == (
        "payment-service", "write")


async def test_a_question_about_docs_becomes_a_doc_question():
    triage = triage_with([
        function_call("create_doc_question_task", {
            "confidence": 0.8, "question": "is the field optional?", "doc_ref": None,
        }, call_id="1")
    ])

    outcome = await decide(triage)

    assert outcome.type == "doc_question"
    assert outcome.params.question == "is the field optional?"


async def test_social_talk_becomes_a_skip():
    triage = triage_with([
        function_call("skip", {"confidence": 0.99, "reason": "lunch plans"},
                      call_id="1")
    ])

    outcome = await decide(triage)

    assert outcome.type == "skip"
    assert outcome.params.reason == "lunch plans"


async def test_a_message_with_no_extractable_parameters_still_decides():
    """'the api is wrong' is the most common shape and carries nothing.
    It must still produce a task — that is what triggers asking for the fields."""
    triage = triage_with([
        function_call("create_api_issue_task", {
            "confidence": 0.6, "summary": "api is wrong",
            "environment": None, "correlation_id": None, "curl": None,
        }, call_id="1")
    ])

    outcome = await decide(triage)

    assert outcome.type == "api_issue"
    assert outcome.params.correlation_id is None


async def test_answering_without_calling_a_tool_asks_for_a_human():
    """Never-drop: an undecided message becomes a task, not silence."""
    triage = triage_with([[assistant_message("I am not sure what this is.")]])

    outcome = await decide(triage)

    assert isinstance(outcome, NeedsHuman)


async def test_a_model_failure_asks_for_a_human():
    triage = triage_with(RuntimeError("provider exploded"))

    outcome = await decide(triage)

    assert isinstance(outcome, NeedsHuman)
    assert "provider exploded" in outcome.reason


async def test_triage_needs_no_database():
    """It reports a decision; applying it belongs to the caller."""
    import inspect

    assert "db" not in inspect.signature(Triage.__init__).parameters


async def test_triage_passes_text_through_without_extracting_fields():
    """Ticket 31 moved field extraction out of triage. Triage now leaves the
    model's first-pass values alone — extraction is the workflow's job, and
    the workflow's extractor uses the LLM, not the regex that used to live
    here. The values the model produced are what triage ships; whatever the
    workflow extracts overlays on top in plan()."""
    triage = triage_with([
        function_call("create_api_issue_task", {
            "confidence": 0.9, "summary": "api broken",
            "environment": "production", "correlation_id": None, "curl": None,
        }, call_id="1")
    ])

    outcome = await decide(triage, text="failing on staging since noon")

    assert outcome.params.environment == "production"


async def test_the_string_null_is_treated_as_absent():
    triage = triage_with([
        function_call("create_api_issue_task", {
            "confidence": 0.6, "summary": "api broken",
            "environment": "null", "correlation_id": "null", "curl": "null",
        }, call_id="1")
    ])

    outcome = await decide(triage, text="the api is wrong")

    assert outcome.params.environment is None
    assert outcome.params.correlation_id is None
    assert outcome.params.curl is None


async def test_hygiene_applies_to_other_task_types_too():
    triage = triage_with([
        function_call("create_doc_question_task", {
            "confidence": 0.8, "question": "  is it optional?  ", "doc_ref": "N/A",
        }, call_id="1")
    ])

    outcome = await decide(triage)

    assert outcome.params.question == "is it optional?"
    assert outcome.params.doc_ref is None


class NeverCalled(Model):
    """A model that fails the test if it is reached."""

    async def get_response(self, *a, **kw):
        raise AssertionError("the model was called")

    def stream_response(self, *a, **kw):
        raise AssertionError("the model was called")


async def test_compensation_talk_is_skipped_without_calling_the_model():
    """Pay talk must not reach a third-party API, and must not depend on the
    model's judgement to be left alone."""
    outcome = await decide(
        Triage(config=CONFIG, model=NeverCalled()), "lương tháng này về chưa"
    )

    assert isinstance(outcome, Decided)
    assert outcome.type == "skip"
    assert outcome.confidence == 1.0


@pytest.mark.parametrize(
    "text",
    [
        "lương tháng này về chưa",
        "khi nào có thưởng tết",
        "did the salary come through?",
        "what's the bonus structure",
        "anh ơi lương",
    ],
)
async def test_compensation_phrasings_are_all_caught(text):
    outcome = await decide(Triage(config=CONFIG, model=NeverCalled()), text)
    assert outcome.type == "skip"


@pytest.mark.parametrize(
    "text",
    [
        "the payment API returns 500",
        "salary-service is down on staging",
        "can you review the bonus calculation endpoint",
    ],
)
async def test_work_about_pay_still_reaches_the_model(text):
    """A pay-related *system* is ordinary work. Only talk about our own pay is
    filtered."""
    triage = triage_with([
        function_call("create_api_issue_task", {
            "confidence": 0.9, "summary": "s", "environment": None,
            "correlation_id": None, "curl": None,
        }, call_id="1")
    ])
    assert (await decide(triage, text)).type == "api_issue"
