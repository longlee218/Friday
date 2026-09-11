"""Asking a model for a shape, and only believing what fits it.

`friday/agent/structured.py` plus `Harness.run_structured`. These replace a
pair of hand-written parsers — one in `friday/extraction/`, one in
`friday/memory/channel_context.py` — that turned whatever a model said into
structure by guessing, could not fail, and checked no types at all.

**The measurement behind the design is worth restating here**, because it is
the reason validation is local rather than `response_format: json_schema`.
Probed against the configured provider (MiniMax-M3) on 2026-09-11: it accepts
a `json_schema` response format without error — `docs/DESIGN.md` feared a 400
and there is none — and then ignores it, answering inside a ```json fence,
after a `<think>` block, with prose following, and naming an enum member that
was not in the enum. A provider that rejects the parameter is one you find
out about; one that accepts and ignores it is one you do not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import pytest

from agents.testing import ScriptedModel, assistant_message, function_call

from friday.agent.harness import Harness
from friday.agent.structured import describe, find_json, fits
from friday.config import AgentConfig


@dataclass
class Picks:
    """A shape whose fields are closed sets — module-level on purpose, so its
    annotations resolve and `describe` reads the real `Literal` rather than
    the source-text fallback (which the test below covers instead)."""

    mode: Literal["strict", "loose"] = "strict"
    many: list[Literal["strict", "loose"]] = field(default_factory=list)


@dataclass
class Shape:
    name: str = ""
    count: int = 0
    tags: list[str] = field(default_factory=list)
    note: str | None = None


# --- find_json: what the provider actually returns --------------------------


def test_a_bare_object_is_found():
    assert find_json('{"name": "x"}') == {"name": "x"}


def test_the_object_is_found_inside_a_fence_after_a_think_block():
    """The exact shape the configured provider returns. `json.loads` raises on
    every part of this, and the summariser — which called it directly — stored
    the whole blob, reasoning included, as what the room was about."""
    assert find_json(
        '<think>\nweighing it up\n</think>\n\n```json\n{"name": "x"}\n```\n\nThat is it.'
    ) == {"name": "x"}


def test_an_unclosed_think_block_swallows_no_answer():
    """The budget ran out mid-thought: there is no answer after it, and
    nothing in the deliberation should be read as one — a `{` inside the
    model's own working-out is not the object it was asked for."""
    assert find_json('<think>\nmaybe {"name": "guess"} would do') is None


def test_prose_with_no_object_is_not_invented():
    assert find_json("xin lỗi, tôi không chắc") is None


def test_a_json_array_is_not_an_object():
    assert find_json('["a", "b"]') is None


def test_braces_are_matched_not_searched_for():
    """The last `}` may belong to prose after the object, and the first `{`
    may open an example inside it."""
    assert find_json('{"name": "x", "tags": ["{"]} and then } trailing') == {
        "name": "x", "tags": ["{"],
    }


# --- fits: the check nothing used to do -------------------------------------


def test_a_matching_object_becomes_an_instance():
    value, problem = fits({"name": "x", "count": 2}, Shape)

    assert problem is None
    assert value == Shape(name="x", count=2)


def test_a_wrong_type_is_refused_rather_than_constructed():
    """The gap this whole change exists for: a dataclass constructor takes
    `name=123` without complaint, and the error surfaces later somewhere with
    no idea a model caused it. Proven against the real failure that was
    reachable in production — `validate()` raising `unhashable type: 'list'`
    from inside node 0, quoted at the operator as a hand-over."""
    value, problem = fits({"name": ["a", "b"]}, Shape)

    assert value is None
    assert "name" in problem


def test_the_reason_names_the_field_so_the_model_can_fix_it():
    _, problem = fits({"count": "not a number"}, Shape)

    assert "count" in problem


def test_an_unknown_key_is_dropped_not_fatal():
    """The one tolerance kept from the parser this replaces, and kept for its
    recorded reason: a single invented key used to raise on the constructor
    and discard the whole extraction, including the fields read correctly."""
    value, problem = fits({"name": "x", "invented": "?"}, Shape)

    assert problem is None
    assert value == Shape(name="x")


def test_an_absent_field_keeps_its_default():
    value, problem = fits({}, Shape)

    assert problem is None
    assert value == Shape()


def test_null_is_accepted_where_the_field_allows_it():
    value, problem = fits({"note": None}, Shape)

    assert problem is None and value.note is None


# --- describe: the prompt's text, generated from the shape ------------------


def test_every_field_is_named_with_its_type():
    said = describe(Shape)

    assert "- name: string" in said
    assert "- count: integer" in said
    assert "- tags: list[string]" in said


def test_a_nullable_field_says_so_in_words_a_model_acts_on():
    """`str | None` rendered as "string or null" is what makes a model write
    `null` instead of inventing a value, which is what every absent field in
    this system relies on."""
    assert "note: string or null" in describe(Shape)


def test_the_field_doc_reaches_the_description_where_there_is_one():
    """The extraction schemas carry a `doc` per field — the same string that
    tells the extractor what the field means. One source, both readers."""
    from friday.domain.models import ApiIssueParams

    said = describe(ApiIssueParams)

    assert "correlation_id" in said
    assert "copied exactly" in said, "the field's own doc is missing"


def test_the_shape_the_summariser_is_told_is_the_one_it_is_checked_against():
    """Two encodings of one contract is the failure this replaced: the four
    summary keys were written in prose in `SUMMARY_JOB` *and* in a
    `SUMMARY_FIELDS` tuple, so a fifth field added to one was invisible to
    the other."""
    from friday.memory.channel_context import RoomSummary, _summary_instructions

    told = _summary_instructions()

    for line in describe(RoomSummary).splitlines():
        assert line in told, line


# --- run_structured: the answer arrives as a tool call ----------------------
#
# Driven through the scripted model transport and a real `Harness`, which is
# the seam the spec names for this group and the highest one available: it
# exercises the tool declaration, the argument validation, the correction turn
# and the final-output handling, none of which a harness double would touch.
# A double that supplied its own structured-answer method could pass while
# skipping the whole mechanism, which is what `ScriptedHarness` exists to warn
# about and what the version of these tests before board
# `every-answer-has-a-shape` actually did.


def _asking(*steps, **options) -> Harness:
    return Harness(
        config=AgentConfig(
            name="shaped", api_key="k", base_url="http://x/v1", model="m",
            **options,
        ),
        instructions="answer the question",
        answers=Shape,
        model=ScriptedModel(list(steps)),
    )


async def test_an_answer_that_fits_costs_one_call():
    harness = _asking([function_call("answer", {"name": "x"}, call_id="1")])

    assert await harness.run_structured("ask") == Shape(name="x")
    assert len(harness.agent.model.calls) == 1, "the common path paid for a retry"


async def test_an_answer_that_does_not_fit_earns_a_correction_inside_the_run():
    """One run, not two. The bad call's own tool output carries the reason back
    to the model, so the correction costs a turn rather than a second
    `timeout_seconds` — which is what the written-answer version of this method
    cost, and what made a structured call two clocks instead of one."""
    harness = _asking(
        [function_call("answer", {"name": ["a"]}, call_id="1")],
        [function_call("answer", {"name": "x"}, call_id="2")],
    )

    assert await harness.run_structured("ask") == Shape(name="x")
    assert len(harness.agent.model.calls) == 2


async def test_a_second_bad_answer_is_not_a_third_attempt():
    """A model that cannot produce its own declared shape twice will not on the
    third go, and this runs on every task in a busy room. The budget is
    `max_turns` and nothing else — there is no retry loop of our own."""
    harness = _asking(
        [function_call("answer", {"name": ["a"]}, call_id="1")],
        [function_call("answer", {"name": ["b"]}, call_id="2")],
        [function_call("answer", {"name": "x"}, call_id="3")],
    )

    assert await harness.run_structured("ask") is None
    assert harness.last_error is not None


async def test_the_correction_names_the_field_and_quotes_nothing_back():
    """The reason goes back as the tool's own output. It names the field,
    because that is what the model needs in order to fix it; it carries none
    of the arguments, because an extractor's whole job is copying a reporter's
    bytes out verbatim, so its own arguments are reporter-controlled text.

    **Asserted on the tool's output, not on the whole request**, and the
    difference is the trust boundary rather than pedantry. The model's own
    failed call is in the run's history and the SDK replays it — that text
    never left the model's context, so nothing crossed a boundary. What the
    deleted written-answer correction did was build a *new* prompt quoting the
    reply, which is reporter-controlled text re-entering from outside. This is
    the half we control, and it is the half that was wrong.
    """
    harness = _asking(
        [function_call("answer", {"count": "nope"}, call_id="1")],
        [function_call("answer", {"count": 1}, call_id="2")],
    )

    await harness.run_structured("ask")

    turned_back = [
        item["output"]
        for item in harness.agent.model.calls[1].input
        if isinstance(item, dict) and item.get("type") == "function_call_output"
    ]

    assert any("count" in said for said in turned_back), (
        "the correction must name the field"
    )
    assert not any("nope" in said for said in turned_back), (
        "the tool quoted the arguments back"
    )


async def test_a_model_that_never_answered_is_not_an_empty_answer():
    """`None` from the run is not a malformed reply — nothing came back."""
    harness = _asking([assistant_message("")])

    assert await harness.run_structured("ask") is None


async def test_an_answer_written_as_text_is_still_read():
    """D13: forcing the tool call is not a guarantee — the same probe that
    found a tool call's arguments clean also found this model answering
    outside a closed enum it had just been given. A reply that arrives as
    prose anyway is found and checked against the same shape, by the same
    helpers, rather than being thrown away."""
    harness = _asking(
        [assistant_message('<think>ok</think>\n```json\n{"name": "x"}\n```\nthat is it')]
    )

    assert await harness.run_structured("ask") == Shape(name="x")


async def test_prose_that_is_not_the_shape_is_none_not_a_default_instance():
    """The hazard this whole method replaces, pinned at the seam: an
    unreadable reply used to become `{}`, every field of a `Params` has a
    default, and nothing downstream could tell it from a model that genuinely
    found nothing."""
    harness = _asking([assistant_message("xin lỗi, tôi không chắc")])

    assert await harness.run_structured("ask") is None


async def test_an_agent_with_no_declared_shape_cannot_be_asked_for_one():
    """The shape is the agent's contract, declared where the agent is built.
    Asking an agent that never declared one is a wiring mistake, and it should
    read as one rather than as a model that would not answer."""
    harness = Harness(
        config=AgentConfig(name="plain", api_key="k", base_url="http://x/v1", model="m"),
        instructions="write prose",
        model=ScriptedModel([[assistant_message("ok")]]),
    )

    with pytest.raises(ValueError, match="answers"):
        await harness.run_structured("ask")


def test_the_answer_tool_carries_each_fields_own_meaning():
    """A tool parameter *is* an instruction to the model, and an undescribed
    one is an instruction to guess — the argument `classify`'s enum
    descriptions already make. The meaning lives on the field, as the same
    `doc` the prompt renders, so there is one source for both readers."""
    from friday.agent.harness import _answer_tool
    from friday.domain.models import ApiIssueParams

    described = _answer_tool(ApiIssueParams).params_json_schema["properties"]

    assert "copied exactly" in described["correlation_id"]["description"]


# --- what an adversarial review of the first version found ------------------


def test_an_object_of_entirely_unknown_keys_is_refused_not_emptied():
    """The first version of this module reintroduced the exact bug it was
    written to delete. Keys are filtered to the ones the shape names, so an
    object with none of them left `{}`, every field has a default, and the
    caller was handed a successful answer of nothing — with no correction
    turn, and, in the extractor's case, an `ExtractionMark` written against
    that fingerprint so the empty answer replayed forever.

    `{"parameters": {...}}` is how a model does this: wrapping the object it
    was asked for."""
    value, problem = fits({"parameters": {"name": "x"}}, Shape)

    assert value is None
    assert "parameters" in problem


def test_a_literally_empty_object_is_still_an_answer():
    """The other side of that line: `{}` is a model saying it found nothing,
    which is an answer, and every field absent is what it means."""
    value, problem = fits({}, Shape)

    assert problem is None and value == Shape()


def test_a_think_tag_inside_a_value_does_not_eat_the_reply():
    """Reasoning is a *prefix*. The first version stripped `<think>` wherever
    it appeared, to end of text when unclosed — so a reporter's pasted
    `<think>`, copied out verbatim by the extractor because that is its whole
    job, destroyed the reply carrying it."""
    assert find_json('{"note": "he wrote <think> in chat"}') == {
        "note": "he wrote <think> in chat"
    }


def test_a_closing_think_tag_with_no_opener_is_still_reasoning():
    """A provider that strips the opening tag on the way out. Everything
    before the bare `</think>` is deliberation, and leaving it in handed back
    the JSON the model was thinking with rather than the one it answered
    with."""
    assert find_json('weighing {"a": 1} up</think> {"b": 2}') == {"b": 2}


def test_a_schema_whose_annotations_do_not_resolve_still_describes():
    """`describe` is prompt-building and must not be the thing that raises. A
    dataclass declared inside a function, annotated against a locally aliased
    import, cannot be resolved by `get_type_hints` — found when a test did
    exactly that and `NameError` came out of the prompt builder."""
    from typing import Optional as _Aliased

    @dataclass
    class Local:
        value: _Aliased[str] = None

    said = describe(Local)

    assert "value" in said


def test_the_shapes_own_docstring_does_not_go_on_the_wire():
    """A dataclass docstring here is developer prose — `RoomSummary`'s runs to
    nine paragraphs about why it has four fields and not six — and pydantic
    puts it on the object as `description`. That would be sent to the provider
    on every call, as tokens and as confusion. What the model needs about the
    shape as a whole is on the tool's description; what it needs about a field
    is on the field."""
    from friday.agent.harness import _answer_params
    from friday.memory.channel_context import RoomSummary

    described = _answer_params(RoomSummary)

    assert "description" not in described
    assert described["properties"]["topic"]["description"]


def test_a_closed_set_is_described_by_its_values_not_by_its_types():
    """A `Literal`'s arguments are *values*, not types. The generic branch
    recursed into them as annotations, which is wrong in principle and wrong
    in practice: the model reads this line, and a member it cannot name is a
    member it cannot pick."""
    said = describe(Picks)

    assert '- mode: "strict" or "loose"' in said
    assert '- many: list["strict" or "loose"]' in said


def test_a_closed_set_survives_the_unresolved_fallback_too():
    """The other path to the same line. A schema whose annotations cannot be
    resolved is described from its source text, and that read `str` as a
    substring — so `Literal["strict", "loose"]` came out as `stringict`, a
    member of a closed set that does not exist. Declared inside a function
    against a locally aliased import, which is what makes `get_type_hints`
    fail and this branch run."""
    from dataclasses import dataclass as _dataclass
    from typing import Literal as _Literal

    @_dataclass
    class Local:
        mode: _Literal["strict", "loose"] = "strict"

    said = describe(Local)

    assert "stringict" not in said
    assert "strict" in said
