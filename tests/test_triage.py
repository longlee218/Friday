"""Ticket 04 slice 2 — the triage agent.

Driven with the SDK's ScriptedModel: no test makes a network call. What is being
tested is our tool surface and how a tool call becomes a decision — not the
model's judgement, which only a live run can speak to.

**Triage classifies and stops.** Which tool it called is the whole answer; the
only thing it adds is how certain it is. Lifting values out of the message is a
different job with a different failure mode and it belongs to whoever needs
those values — see `friday/extraction.py`. These tests hold that line, because
the tool schema is the only thing stopping a model from being asked to do both.
"""

from __future__ import annotations

import pytest
from agents.models.interface import Model
from agents.testing import ScriptedModel, assistant_message, function_call

from conftest import make_event
from friday.config import AgentConfig
from friday.triage import Decided, NeedsHuman, Triage
from friday.triage.prefilter import Sensitive

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


async def test_an_api_problem_becomes_an_api_issue():
    triage = triage_with([
        function_call("create_api_issue_task", {"confidence": 0.9}, call_id="1")
    ])

    outcome = await decide(triage)

    assert isinstance(outcome, Decided)
    assert outcome.type == "api_issue"
    assert outcome.confidence == 0.9


async def test_a_permission_request_becomes_an_access_request():
    triage = triage_with([
        function_call("create_access_request_task", {"confidence": 0.95}, call_id="1")
    ])

    assert (await decide(triage)).type == "access_request"


async def test_a_question_about_docs_becomes_a_doc_question():
    triage = triage_with([
        function_call("create_doc_question_task", {"confidence": 0.8}, call_id="1")
    ])

    assert (await decide(triage)).type == "doc_question"


async def test_social_talk_becomes_a_skip():
    triage = triage_with([
        function_call("skip", {"confidence": 0.99}, call_id="1")
    ])

    assert (await decide(triage)).type == "skip"


async def test_a_message_carrying_nothing_still_decides():
    """"the api is wrong" is the most common shape there is and carries no
    values at all. It must still produce a task — that is what triggers asking
    for the fields, and it is why classifying does not depend on extracting."""
    triage = triage_with([
        function_call("create_api_issue_task", {"confidence": 0.6}, call_id="1")
    ])

    assert (await decide(triage)).type == "api_issue"


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


def test_no_triage_tool_asks_for_anything_but_confidence():
    """The line, held by the only thing that can hold it.

    A tool parameter is an instruction to the model, so a schema with
    `correlation_id` in it *is* triage extracting, whatever the prompt says.
    Adding one back would put two producers on one field again — and the merge
    that reconciled them cost nineteen direct messages about one report before
    it was removed.
    """
    import inspect

    from friday.triage import TOOLS

    for tool in TOOLS:
        taken = set(inspect.signature(tool.on_invoke_tool).parameters)
        params = set(getattr(tool, "params_json_schema", {}).get("properties", {}))
        assert params <= {"confidence"}, f"{tool.name} also asks for {params}"


class NeverCalled(Model):
    """A model that fails the test if it is reached."""

    async def get_response(self, *a, **kw):
        raise AssertionError("the model was called")

    def stream_response(self, *a, **kw):
        raise AssertionError("the model was called")


WORDS = Sensitive(["lương", "thưởng", "salary", "bonus", "mật khẩu", "xin api key"])


def guarded() -> Triage:
    """A triage whose model fails the test if it is reached."""
    return Triage(config=CONFIG, model=NeverCalled(), sensitive=WORDS)


async def test_a_sensitive_message_never_reaches_the_model():
    """The harm is in the sending, so the decision is made before the call —
    by a rule a persuasive message cannot argue with."""
    outcome = await decide(guarded(), "lương tháng này về chưa")

    assert isinstance(outcome, NeedsHuman)


async def test_it_is_held_for_the_operator_and_not_dropped():
    """`NeedsHuman` opens a task in the column the operator watches. This list
    contains words that appear in ordinary reports — "cho em xin api key của
    staging" is an access request — and skipping them would be losing real
    mentions on the strength of one word. The guarantee is that the *model*
    does not see it."""
    outcome = await decide(guarded(), "cho em xin api key của staging")

    assert isinstance(outcome, NeedsHuman)
    assert "xin api key" in outcome.reason


def test_the_reason_names_the_word_and_not_the_message():
    """The message is the thing being kept quiet. It must not be copied into
    an explanation that then travels."""
    said = "mật khẩu prod là hunter2"

    assert said not in WORDS.found(said)


@pytest.mark.parametrize(
    "text",
    [
        "lương tháng này về chưa",
        "luong thang nay ve chua",          # no diacritics — how half of it is typed
        "LƯƠNG tháng này?",                 # shouting
        "khi nào có thưởng tết",
        "did the salary come through?",
        "what's the BONUS structure",
        "đổi mật khẩu DB giúp em",
    ],
)
async def test_every_phrasing_of_a_listed_word_is_caught(text):
    assert isinstance(await decide(guarded(), text), NeedsHuman)


@pytest.mark.parametrize(
    "text",
    [
        "the payment API returns 500",
        "salary-service is down on staging",   # a system, not a payday
        "can you review the bonuses endpoint",  # `bonuses` is not `bonus`
    ],
)
async def test_a_word_inside_an_identifier_is_not_the_word(text):
    """A hyphen or a suffix makes it a name. Holding every message about
    `salary-service` would make the list unusable in a codebase that has one."""
    triage = triage_with(
        [function_call("create_api_issue_task", {"confidence": 0.9}, call_id="1")],
    )
    triage._sensitive = WORDS

    assert (await decide(triage, text)).type == "api_issue"


async def test_an_empty_list_holds_nothing():
    """An install that has not thought about this yet gets what it would have
    had without the feature, not someone else's guesses about what is sensitive
    in their workplace."""
    triage = triage_with(
        [function_call("create_api_issue_task", {"confidence": 0.9}, call_id="1")],
    )

    assert (await decide(triage, "lương tháng này về chưa")).type == "api_issue"
