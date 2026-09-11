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

import pytest

from conftest import ScriptedHarness
from friday.agent.structured import describe, find_json, fits


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


# --- run_structured: validate, then one correction turn ---------------------


class _Says(ScriptedHarness):
    """Answers from a script, one reply per call, and counts the calls."""

    def __init__(self, *answers):
        super().__init__()
        self.answers = list(answers)
        self.prompts: list[str] = []

    async def run(self, prompt, **kwargs):
        self.prompts.append(prompt)
        answer = self.answers.pop(0) if self.answers else None
        return None if answer is None else type("R", (), {"final_output": answer})()


async def test_an_answer_that_fits_costs_one_call():
    harness = _Says('{"name": "x"}')

    assert await harness.run_structured("ask", Shape) == Shape(name="x")
    assert len(harness.prompts) == 1, "the common path must not pay for a retry"


async def test_an_answer_that_does_not_fit_earns_exactly_one_correction():
    harness = _Says('{"name": ["a"]}', '{"name": "x"}')

    assert await harness.run_structured("ask", Shape) == Shape(name="x")
    assert len(harness.prompts) == 2


async def test_a_second_bad_answer_is_not_a_third_attempt():
    """A model that cannot produce its own declared shape twice will not on
    the third go, and this runs on every task in a busy room."""
    harness = _Says('{"name": ["a"]}', '{"name": ["b"]}')

    assert await harness.run_structured("ask", Shape) is None
    assert len(harness.prompts) == 2


async def test_the_correction_tells_the_model_what_was_wrong():
    harness = _Says('{"count": "nope"}', '{"count": 1}')

    await harness.run_structured("ask", Shape)

    correction = harness.prompts[1]
    assert "count" in correction, "the correction must name the field"
    assert "ask" in correction, "the original request has to be carried again"
    assert "nope" not in correction, (
        "the failed reply must not be quoted back: the extractor copies a "
        "reporter's bytes verbatim, so its own reply is reporter-controlled "
        "text and would re-enter the prompt outside the trust boundary"
    )


async def test_a_model_that_never_answered_is_not_corrected():
    """`None` from `run` is not a malformed reply — nothing came back, and
    asking again would spend a second call on the same outage."""
    harness = _Says(None)

    assert await harness.run_structured("ask", Shape) is None
    assert len(harness.prompts) == 1


async def test_an_empty_reply_is_never_a_successful_empty_answer():
    """The hazard this replaces, pinned at the seam: an unreadable reply used
    to become `{}`, and every field of a `Params` has a default, so nothing
    downstream could tell it from a model that genuinely found nothing."""
    harness = _Says("", "")

    assert await harness.run_structured("ask", Shape) is None


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
