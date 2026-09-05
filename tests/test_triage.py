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
        function_call("classify", {"task_type": "api_issue", "confidence": 0.9}, call_id="1")
    ])

    outcome = await decide(triage)

    assert isinstance(outcome, Decided)
    assert outcome.type == "api_issue"
    assert outcome.confidence == 0.9


async def test_a_permission_request_becomes_an_access_request():
    triage = triage_with([
        function_call("classify", {"task_type": "access_request", "confidence": 0.95}, call_id="1")
    ])

    assert (await decide(triage)).type == "access_request"


async def test_a_question_about_docs_becomes_a_doc_question():
    triage = triage_with([
        function_call("classify", {"task_type": "doc_question", "confidence": 0.8}, call_id="1")
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
        function_call("classify", {"task_type": "api_issue", "confidence": 0.6}, call_id="1")
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


def test_no_triage_tool_asks_for_anything_but_a_type_and_confidence():
    """The line, held by the only thing that can hold it.

    A tool parameter is an instruction to the model, so a schema with
    `correlation_id` in it *is* triage extracting, whatever the prompt says.
    Adding one back would put two producers on one field again — and the merge
    that reconciled them cost nineteen direct messages about one report before
    it was removed.

    `task_type` is not extraction: it is what used to be encoded as *which*
    tool got called, and ticket 08 made it a parameter of one tool instead.
    """
    from friday.triage import TOOLS

    for tool in TOOLS:
        params = set(getattr(tool, "params_json_schema", {}).get("properties", {}))
        assert params <= {"confidence", "task_type"}, f"{tool.name} also asks for {params}"


def test_create_task_describes_every_type_from_its_own_params_class():
    """D16: adding a task type is adding one `Params` class, not a class and a
    second description of it here. `create_task`'s enum and its per-value
    description are both read out of `PARAMS` at import time — this pins that
    nobody hand-wrote either and let them drift."""
    from friday.triage import TOOLS
    from friday.domain.models import PARAMS

    (create_task,) = [t for t in TOOLS if t.name == "classify"]
    schema = create_task.params_json_schema["properties"]["task_type"]

    assert set(schema["enum"]) == set(PARAMS)
    for name, cls in PARAMS.items():
        assert cls.__doc__.strip() in schema["description"], (
            f"{name}'s own docstring is not in create_task's description"
        )


class NeverCalled(Model):
    """A model that records being reached, and refuses to answer.

    It **counts** rather than only raising, and the difference is the whole
    reason this class is written down. Raising looks like it fails the test —
    it does not: `Harness` turns any failure into `None`, and the caller turns
    that into `NeedsHuman`, which is the *same outcome the prefilter produces*.
    A test asserting only on the outcome cannot tell "held before the call"
    from "sent, and the provider exploded", and the whole point of the
    prefilter is which of those two happened.

    Verified: with `Sensitive.found` stubbed to `return None`, eight tests here
    still passed and this was reached eight times.
    """

    def __init__(self) -> None:
        self.reached = 0

    async def get_response(self, *a, **kw):
        self.reached += 1
        raise AssertionError("the model was called")

    def stream_response(self, *a, **kw):
        self.reached += 1
        raise AssertionError("the model was called")


WORDS = Sensitive(["lương", "thưởng", "salary", "bonus", "mật khẩu", "xin api key"])


def guarded() -> tuple[Triage, NeverCalled]:
    """A triage and the model it must not reach. Both are returned, because
    the outcome alone does not say whether it was reached."""
    model = NeverCalled()
    return Triage(config=CONFIG, model=model, sensitive=WORDS), model


async def test_a_sensitive_message_never_reaches_the_model():
    """The harm is in the sending, so the decision is made before the call —
    by a rule a persuasive message cannot argue with.

    Asserted on the *model*, not on the outcome. The outcome is `NeedsHuman`
    either way: that is what the prefilter produces, and it is also what a
    failed call produces."""
    triage, model = guarded()

    outcome = await decide(triage, "lương tháng này về chưa")

    assert model.reached == 0, "the message was sent to the model"
    assert isinstance(outcome, NeedsHuman)


async def test_it_is_held_for_the_operator_and_not_dropped():
    """`NeedsHuman` opens a task in the column the operator watches. This list
    contains words that appear in ordinary reports — "cho em xin api key của
    staging" is an access request — and skipping them would be losing real
    mentions on the strength of one word. The guarantee is that the *model*
    does not see it."""
    triage, model = guarded()

    outcome = await decide(triage, "cho em xin api key của staging")

    assert model.reached == 0
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
    triage, model = guarded()

    assert isinstance(await decide(triage, text), NeedsHuman)
    assert model.reached == 0, f"{text!r} was sent to the model"


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
        [function_call("classify", {"task_type": "api_issue", "confidence": 0.9}, call_id="1")],
    )
    triage._sensitive = WORDS

    assert (await decide(triage, text)).type == "api_issue"


async def test_an_empty_list_holds_nothing():
    """An install that has not thought about this yet gets what it would have
    had without the feature, not someone else's guesses about what is sensitive
    in their workplace."""
    triage = triage_with(
        [function_call("classify", {"task_type": "api_issue", "confidence": 0.9}, call_id="1")],
    )

    assert (await decide(triage, "lương tháng này về chưa")).type == "api_issue"


async def test_a_malformed_classify_call_ends_the_run():
    """`stop_on_first_tool` cannot tell a failed tool from a successful one.

    The first tool call's output is the run's final output, and a tool's
    failure message is its output — so a `classify` call the schema rejects
    ends the run with nothing recorded, and the model is never asked again.
    The corrected call scripted below is never requested.

    Pinned because the tool layer now goes out of its way to let a model fix
    its own malformed call (`harness._tool_failed` hands a `ModelBehaviorError`
    back in the SDK's "try again" wording), and it is worth one test saying out
    loud that the wording never reaches this agent. Nothing is lost — the
    mention goes to a person — but it goes there for something the model could
    have fixed, and if that is ever to change it changes here.
    """
    from agents.testing import ScriptedModel, function_call

    triage = Triage(
        config=CONFIG,
        model=ScriptedModel(
            [
                [function_call("classify", {"task_type": "api_issue",
                                            "confidence": "high"}, call_id="1")],
                [function_call("classify", {"task_type": "api_issue",
                                            "confidence": 0.9}, call_id="2")],
            ]
        ),
    )

    outcome = await triage.decide(make_event(text="the api is 500ing"))

    assert isinstance(outcome, NeedsHuman)
    assert outcome.reason == "triage produced no classification"
