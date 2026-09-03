"""`ask_clarification` — the tool an agent calls instead of guessing.

The thing worth pinning is not that the tool exists but that **its schema
teaches**. A tool parameter is an instruction to the model: an enum member
nobody defined is an instruction to guess which one fits, and the guess is
invisible afterwards because every value is spelled correctly.
"""

from __future__ import annotations

from friday.tools.clarify import (
    CLARIFICATION_TYPES,
    Clarification,
    ClarifyCapture,
    ask_clarification,
)


def test_every_kind_carries_its_meaning_into_the_schema():
    """The model sees five strings otherwise. It used to: the meanings lived
    in comments above the `Literal` and never left the file."""
    described = ask_clarification.params_json_schema["properties"][
        "clarification_type"
    ]["description"]

    for name, meaning in CLARIFICATION_TYPES.items():
        assert name in described, name
        # Not just the name — the sentence that separates it from its
        # neighbours. `approach_choice` and `ambiguous_requirement` are one
        # word apart and mean different things.
        assert meaning.split(" — ")[0][:40] in described, name


def test_the_enum_and_the_descriptions_cannot_drift():
    """Two lists is one list that goes wrong. Both come off the same dict."""
    offered = ask_clarification.params_json_schema["properties"][
        "clarification_type"
    ]["enum"]

    assert set(offered) == set(CLARIFICATION_TYPES)


def test_a_kind_outside_the_set_is_not_offered():
    offered = ask_clarification.params_json_schema["properties"][
        "clarification_type"
    ]["enum"]

    assert "whatever" not in offered
    assert len(offered) == 5


async def test_the_call_records_what_was_asked_and_why():
    """Driven through a real agent calling the tool, the same seam the graph's
    own tools are tested at — a hand-built context is a different code path.

    The kind is kept whole rather than folded into the question: it is the
    part an operator can count across a week to see what the agent keeps not
    being told.
    """
    from agents.testing import ScriptedModel, function_call

    from friday.agent.harness import Harness
    from friday.config import AgentConfig

    capture = ClarifyCapture()
    agent = Harness(
        config=AgentConfig(
            name="asker", api_key="k",
            base_url="https://example.invalid/v1", model="test-model",
        ),
        instructions="ask if unsure",
        tools=[ask_clarification],
        context_type=ClarifyCapture,
        tool_use_behavior={"stop_at_tool_names": ["ask_clarification"]},
        model=ScriptedModel([[function_call("ask_clarification", {
            "question": "Môi trường nào?",
            "clarification_type": "approach_choice",
            "context": "",
            "options": ["production", "staging"],
        }, call_id="1")]]),
    )

    await agent.run("deploy nó đi", context=capture, extra_turns=2)

    assert capture.clarification == Clarification(
        question="Môi trường nào?",
        kind="approach_choice",
        context="",
        options=("production", "staging"),
    )


def test_the_options_are_offered_to_whoever_answers():
    """Somebody answering "which environment?" should be able to read the
    list and pick, not compose a sentence."""
    asked = Clarification(
        "Môi trường nào?", "approach_choice", options=("production", "staging")
    ).as_ask()

    assert "Môi trường nào?" in asked.text
    assert "production / staging" in asked.text
