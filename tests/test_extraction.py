"""Ticket 31 — workflow-owned extraction.

The extraction module is the second half of the seam. It must:
- register an extractor for a task type
- parse a model's output into a Params instance
- return None on any failure (the workflow falls back to structural only)
- run before validate, so hallucinated values are still caught

Ticket 05 adds `ask_clarification`, so `extract()`/`Extractor.run()` return a
`(Params | None, Clarify | None)` pair rather than a bare `Params | None` —
whether the model asked is independent of whether it also wrote usable field
text.
"""

from __future__ import annotations

import asyncio

import pytest

from conftest import ScriptedHarness
from friday.agent.harness import Harness, Refused
from dataclasses import dataclass, field
from typing import Optional

from friday.extraction import (
    build_extractor,
    extract,
    registered,
)
from friday.extraction.answer import answer_shape
from friday.extraction.context import FullContext
from friday.domain.validation import Matches


def _context(text="", params_cls=None, *, asked=(), memories=(), known=None):
    """A `FullContext` built the way `build_full_context` would, for tests
    that only care about rendering, not gathering. Mirrors the shape
    `build_input`'s five arguments used to have before ticket 15's D26."""
    if known is None:
        known = params_cls() if params_cls is not None else None
    return FullContext(
        transcript=text, domain_memories=memories, asked=asked, known=known
    )


#: The four `_parse` tests that lived here are gone with the parser they
#: covered. Two of them pinned the `key: value` line scraper — reading
#: `environment: production` out of prose the model wrote instead of the JSON
#: it was asked for — which is the guessing this change removes: an answer
#: that is not the declared shape earns one correction turn now, not an
#: interpretation. What remains of the old `_json_object` is
#: `friday/agent/structured.py`'s `find_json`, covered by
#: `tests/test_structured.py`, because a fenced reply after a `<think>` block
#: is what the provider actually returns and reading that is not guesswork.


# --- registration -----------------------------------------------------


def test_extractor_decorator_registers_under_task_type():
    @dataclass
    class FakeParams:
        environment: Optional[str] = None

    class StubHarness(ScriptedHarness):
        #: What a real `Harness` with no skill library has. A stub with
        #: fewer attributes than the type it stands in for passes and is
        #: describing itself.
        tool_turns = 0

        async def run(self, *a, **kw):
            return None

    ext = build_extractor(
        params_cls=FakeParams,
        harness=StubHarness(answers=answer_shape(FakeParams)),  # type: ignore[arg-type]
        name="fake_test_type_31",
    )
    _install("fake_test_type_31", ext)

    try:
        assert "fake_test_type_31" in registered()
    finally:
        registered().pop("fake_test_type_31", None)


def test_registering_twice_replaces_rather_than_raises():
    """`extractor()` refused a duplicate. `register()` — the path the
    composition root takes — does not, and could not: it may run twice in one
    process, and the second run has to replace the first.

    The guard was real behaviour that only tests could reach, and the test that
    held it was the only reason anyone would think the live path had it.
    """
    from dataclasses import dataclass

    @dataclass
    class Fake:
        environment: Optional[str] = None

    class Silent:
        answers = answer_shape(Fake)

        async def run(self, *a, **kw):
            return None

    ext = build_extractor(params_cls=Fake, harness=Silent(), name="second")
    _install("replaced_test_31", ext)

    try:
        _install("replaced_test_31", ext)
        assert registered()["replaced_test_31"] is ext
    finally:
        from friday.extraction import _EXTRACTORS

        _EXTRACTORS.pop("replaced_test_31", None)


def test_an_extractor_returns_a_params_instance_filled_from_model_output():
    """Build an Extractor whose Harness returns a known string; assert it parses
    into the right Params class. Uses a stub Harness so the test does not depend
    on the agents SDK's ScriptedModel shape (which requires tool calls, not raw
    text)."""

    @dataclass
    class ParamsWithRules:
        #: `ask` and not only a name: a field with no phrase beside it is not
        #: askable (`askable_fields`), and a stub with nothing askable builds
        #: an empty enum for `ask_about`, which pydantic refuses.
        environment: Optional[str] = field(
            default=None, metadata={"ask": "which environment you're on"}
        )

    class StubResult:
        output = '{"environment": "production"}'

    class StubHarness(ScriptedHarness):
        tool_turns = 0

        last_error = None

        async def run(self, prompt, **kwargs):
            return StubResult()

    ext = build_extractor(
        params_cls=ParamsWithRules, harness=StubHarness(answers=answer_shape(ParamsWithRules)), name="stub"  # type: ignore[arg-type]
    )
    _install("stub_test_31", ext)

    try:
        params, clarify = asyncio.run(
            extract("stub_test_31", _context("the api is wrong", ParamsWithRules))
        )
        assert isinstance(params, ParamsWithRules)
        assert params.environment == "production"
        assert clarify is None
    finally:
        registered().pop("stub_test_31", None)


def test_an_extractor_returns_none_when_harness_fails():
    class FailingHarness(ScriptedHarness):
        tool_turns = 0

        last_error = "boom"
        #: The model was asked and could not answer, which is not the same as
        #: not asking it — see `Refused`. This is the first of the two.
        refusal = None

        async def run(self, prompt, **kwargs):
            return None

    @dataclass
    class Params:
        environment: Optional[str] = None

    ext = build_extractor(
        params_cls=Params, harness=FailingHarness(answers=answer_shape(Params)), name="fail"  # type: ignore[arg-type]
    )
    _install("failing_test_31", ext)

    try:
        params, clarify = asyncio.run(extract("failing_test_31", _context("x", Params)))
        assert params is None
        assert clarify is None
    finally:
        registered().pop("failing_test_31", None)


def test_an_extractor_returns_none_when_output_does_not_parse():
    class StubResult:
        output = "not even close to JSON"

    class StubHarness(ScriptedHarness):
        tool_turns = 0

        async def run(self, prompt, **kwargs):
            return StubResult()

    @dataclass
    class StrictParams:
        required_id: str  # not Optional - missing raises TypeError

    ext = build_extractor(
        params_cls=StrictParams, harness=StubHarness(answers=answer_shape(StrictParams)), name="bad"  # type: ignore[arg-type]
    )
    _install("bad_output_test_31", ext)

    try:
        # Output is not JSON and has no key:value lines, so _parse returns {}.
        # The Params constructor then fails with TypeError on the missing
        # required field; Extractor returns None.
        params, clarify = asyncio.run(
            extract("bad_output_test_31", _context("x", known=StrictParams(required_id="")))
        )
        assert params is None
        assert clarify is None
    finally:
        registered().pop("bad_output_test_31", None)


async def test_extract_returns_none_for_unregistered_task_type():
    assert await extract(
        "not_a_real_task_type_31_xyz", _context("anything")
    ) == (None, None)


# --- what it wants to ask about is part of the answer (D7) ------------------


async def test_the_extractor_can_ask_for_specific_fields_it_read_it_needs():
    """The scripted-model seam: one tool call carries the fields it read *and*
    the fields it wants the reporter asked about. `Extractor.run` splits that
    into the pair the graph acts on — intent, not words: which of the type's
    own fields, and why.

    Two mechanisms until ticket 08 (D7): the field text was scraped out of a
    written reply, and the request was a second tool writing into a per-run
    capture the caller read back afterwards — so neither half was the return
    value of anything.
    """
    from friday.sdk.testing import ScriptedModel, function_call

    from friday.config import AgentConfig
    from friday.domain.models import ApiIssueParams
    from friday.extraction.answer import Clarify, answer_shape

    ext = build_extractor(
        params_cls=ApiIssueParams,
        harness=Harness(
            config=AgentConfig(
                name="api_issue_ext", api_key="k",
                base_url="https://example.invalid/v1", model="test-model",
            ),
            instructions="extract",
            answers=answer_shape(ApiIssueParams),
            model=ScriptedModel([[function_call("answer", {
                "summary": "api trả 500",
                "environment": "production",
                "ask_about": ["curl"],
                "because": "no request anywhere in the report",
            }, call_id="1")]]),
        ),
        name="api_issue_ext",
    )
    _install("clarify_test_31", ext)

    try:
        params, clarify = await extract(
            "clarify_test_31", _context("the api is broken", ApiIssueParams)
        )
        assert params.environment == "production", (
            "the fields it did read came back in the same answer"
        )
        assert clarify == Clarify(
            fields=("curl",), because="no request anywhere in the report"
        )
    finally:
        registered().pop("clarify_test_31", None)


async def test_asking_about_nothing_is_not_a_request_with_no_fields_in_it():
    """An empty `ask_about` is the model saying there is nothing worth asking,
    which the graph must not turn into a question. `because` on its own is not
    a request either — the fields are what a question gets built from."""
    from friday.sdk.testing import ScriptedModel, function_call

    from friday.config import AgentConfig
    from friday.domain.models import ApiIssueParams
    from friday.extraction.answer import answer_shape

    ext = build_extractor(
        params_cls=ApiIssueParams,
        harness=Harness(
            config=AgentConfig(
                name="api_issue_ext", api_key="k",
                base_url="https://example.invalid/v1", model="test-model",
            ),
            instructions="extract",
            answers=answer_shape(ApiIssueParams),
            model=ScriptedModel([[function_call("answer", {
                "summary": "api trả 500", "because": "everything was there",
            }, call_id="1")]]),
        ),
        name="quiet",
    )
    _install("quiet_test", ext)

    try:
        _, clarify = await extract("quiet_test", _context("x", ApiIssueParams))
        assert clarify is None
    finally:
        registered().pop("quiet_test", None)


def test_an_extractor_cannot_ask_about_a_field_that_does_not_exist():
    """The closed set is the enforcement, and it survived the move off the
    tool: `ask_about` is generated from the type's askable fields, so the
    schema itself is what stops the model asking about something that is not
    there, something it writes itself, or something nobody is ever asked for.

    **Written out rather than derived**, because the derivation is what is
    under test. The sets moved in ticket 01: `summary` is excluded because
    the model writes it, and `correlation_id` because a reporter never has
    one to give — it is read out of the response they pasted, and the three
    messages this system has ever sent asking for one had to teach the
    reporter where to look.
    """
    from friday.agent.harness import _answer_params
    from friday.domain.models import AccessRequestParams, ApiIssueParams, DocQuestionParams
    from friday.extraction.answer import answer_shape

    for params_cls, askable in (
        (ApiIssueParams, {"environment", "response", "endpoint", "identifier", "curl"}),
        (AccessRequestParams, {"project", "permission"}),
        (DocQuestionParams, {"question", "doc_ref"}),
    ):
        schema = _answer_params(answer_shape(params_cls))
        enum = set(schema["properties"]["ask_about"]["items"]["enum"])
        assert enum == askable, params_cls.__name__


async def test_a_field_the_type_does_not_have_is_refused_rather_than_asked_about():
    """Not only declared closed — checked. The provider is measured to ignore
    a schema it has just been given, so the enum on the wire is a suggestion;
    what makes this safe is that the arguments are validated in this process,
    and an invented name costs the model its correction turn rather than
    costing the reporter a question about nothing."""
    from friday.agent.structured import fits
    from friday.domain.models import ApiIssueParams
    from friday.extraction.answer import answer_shape

    value, problem = fits({"ask_about": ["deployment_colour"]}, answer_shape(ApiIssueParams))

    assert value is None
    assert "ask_about" in problem.fields


# --- triage classifies; extraction is the only producer ---------------------


def test_one_extractor_block_serves_every_classifiable_type():
    """Triage fills nothing in. A task type with no extractor opens with no
    parameters at all and the reporter is asked for what they just said — so
    the block is not optional.

    **One block, not one per type.** It was `extractor_api_issue`,
    `extractor_access_request` and `extractor_doc_question`, on the argument
    that the jobs differ — reading a correlationId is not reading a repo name
    — and that a type could therefore want its own model. In practice all
    three held identical values, so it was one configuration written three
    times and three places to edit when the provider changes. The divergence
    it was reserving is available again the day somebody actually needs it,
    by splitting the key back out; reserving it in advance bought nothing.
    """
    import os

    from friday.config import load_config
    from friday.dag import registry

    for key in ("TRIAGE_API_KEY", "RESPONDER_API_KEY"):
        os.environ.setdefault(key, "test-key")
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    agents = load_config(repo / "config.yaml").agents

    assert "extractor" in agents, "no extractor configured at all"
    assert registry.decision_params(), "no task types registered to extract for"
    stale = [name for name in agents if name.startswith("extractor_")]
    assert stale == [], f"per-type extractor blocks are gone; found {stale}"


def test_register_extractors_covers_every_registered_task_type():
    """Every task type that opens a task has an extractor filling its fields —
    the property the old `EXTRACTS == PARAMS` check bought, now that the extractor
    set is driven by the registry rather than a second hand-maintained map."""
    import os
    from pathlib import Path

    from friday.config import load_config
    from friday.dag import registry
    from friday.extraction import register_extractors, registered

    for key in ("TRIAGE_API_KEY", "RESPONDER_API_KEY", "EXTRACTOR_API_KEY"):
        os.environ.setdefault(key, "test-key")
    config = load_config(Path(__file__).resolve().parents[1] / "config.yaml")

    register_extractors(config)

    # Coverage, not equality: `registered()` is a process-global the other
    # extraction tests also write, so what matters is that every registered task
    # type got an extractor, not that nothing else is present.
    assert set(registry.decision_params()) <= set(registered())


def test_the_string_null_is_treated_as_absent():
    """The live provider taught us this: the model writes the *string* "null"
    often enough that an unnormalised value is mistaken for a real one, and a
    workflow that believes it has a correlationId never asks for the one it
    needs. The check used to live in triage, which no longer produces values."""
    from friday.extraction import _hygiene
    from friday.domain.models import ApiIssueParams

    cleaned = _hygiene(
        ApiIssueParams(
            summary="s", environment="null", correlation_id="N/A", curl="   "
        )
    )

    assert (cleaned.environment, cleaned.correlation_id, cleaned.curl) == (
        None, None, None
    )


def test_values_are_trimmed():
    from friday.extraction import _hygiene
    from friday.domain.models import DocQuestionParams

    assert _hygiene(DocQuestionParams("  is it optional?  ")).question == (
        "is it optional?"
    )


def _install(task_type, ext):
    """Put an extractor in the registry, the way `register()` does.

    There was an `extractor(task_type, ext)` in the module for this — called by
    nothing but these tests, documented as a decorator, and shaped like a
    function. It also refused a duplicate, a policy `register()` does not share
    and could not: the composition root may run twice in one process, and the
    second run has to replace the first rather than raise.
    """
    from friday.extraction import _EXTRACTORS

    _EXTRACTORS[task_type] = ext



def test_every_extraction_field_tells_the_model_what_it_means():
    """The prompt's schema line for a field is its `doc` metadata. Without it
    the model was shown "- summary: summary" — the mechanism existed and
    nothing fed it, because the descriptions lived in triage's tool docstrings
    and were deleted with them instead of moved here."""
    from dataclasses import fields as dataclass_fields

    from friday.dag import registry

    undocumented = [
        f"{cls.__name__}.{f.name}"
        for cls in set(registry.decision_params().values())
        for f in dataclass_fields(cls)
        if not (f.metadata or {}).get("doc")
    ]

    assert undocumented == [], f"fields the extractor cannot explain: {undocumented}"


def test_the_doc_reaches_the_extractors_prompt():
    from friday.domain.models import ApiIssueParams
    from friday.extraction.prompt import build_input

    prompt = build_input(_context("API lỗi", ApiIssueParams))

    assert "copied exactly" in prompt
    assert "- summary: summary" not in prompt


# --- known drops an already-filled field from the schema (ticket 08, D8) ---


def test_a_field_already_known_drops_out_of_the_schema():
    from friday.domain.models import ApiIssueParams
    from friday.extraction.prompt import build_input

    known = ApiIssueParams(environment="production")

    prompt = build_input(_context("API lỗi", known=known))

    assert "- environment:" not in prompt
    assert "- summary:" in prompt, "a still-blank field must stay in the schema"


def test_a_freshly_constructed_known_shows_every_field():
    """`context.known` is never `None` (ticket 15, D26) — a task with
    nothing filled in yet is `params_cls()`, every field its own default —
    so this, not an absent `known`, is what "nothing known yet" looks like
    now. A room with no rows already gets the same guarantee for
    `room_facts`."""
    from friday.domain.models import ApiIssueParams
    from friday.extraction.prompt import build_input

    prompt = build_input(_context("API lỗi", ApiIssueParams))

    assert "- environment:" in prompt
    assert "- correlation_id:" in prompt


def test_an_empty_string_field_is_not_treated_as_known():
    """`_fill`'s own rule — an empty string is not a value someone supplied —
    applies here too: a field the model once wrote `""` for is still blank
    and still worth asking the schema to name."""
    from friday.domain.models import ApiIssueParams
    from friday.extraction.prompt import build_input

    known = ApiIssueParams(environment="")

    prompt = build_input(_context("API lỗi", known=known))

    assert "- environment:" in prompt


async def test_a_model_that_could_not_answer_is_not_a_refusal():
    """The two look identical from here — no result either way — and they are
    told apart by which of them we caused. A model that failed is worth asking
    the reporter about; a call we declined to make is not."""
    from dataclasses import dataclass as _dataclass

    from friday.extraction import build_extractor

    class Refuses(ScriptedHarness):
        tool_turns = 0

        last_error = "over budget"
        refusal = "an-agent has spent 999 of its 10 tokens today"

        async def run(self, prompt, **kwargs):
            return None

    @_dataclass
    class Params:
        environment: str | None = None

    ext = build_extractor(params_cls=Params, harness=Refuses(answers=answer_shape(Params)), name="refuses")

    with pytest.raises(Refused, match="tokens today"):
        await ext.run(_context("anything", Params))


# --- the room reaches the extractor (ticket 01) ---------------------------


def _rows(*lines, origin="admin", channel_id="watched", kind="fact"):
    """What `db.domain_memories` hands back for a room — the operator's rows
    by default, which is what a channel file's `overrides` were until board
    `read-it-the-way-the-operator-does`, ticket 10."""
    from datetime import datetime, timezone

    from friday.domain.models import Memory

    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    return tuple(
        Memory(
            id=f"r{n}", channel_id=channel_id, agent="operator", text=line,
            kind=kind, created_at=now, updated_at=now, origin=origin,
        )
        for n, line in enumerate(lines)
    )


def test_a_room_with_no_rows_leaves_the_prompt_exactly_as_it_was():
    """The tracer bullet must not change the prompt of a room nobody has
    written anything about, and "not much" is not the same as "not at all":
    every extractor in every unconfigured install shares this prefix."""
    from friday.domain.models import ApiIssueParams
    from friday.extraction.prompt import build_input

    said = build_input(_context("API lỗi", ApiIssueParams, memories=()))

    assert "<memory>" not in said
    assert said == build_input(_context("API lỗi", ApiIssueParams))


def test_the_rooms_facts_reach_the_input_and_not_the_instructions():
    """D21. Memory is injected rather than fetched, because the extractor has
    two turns and has been seen spending both on skill calls — but it goes on
    the channel a model reads as somebody speaking, not the one it reads as
    its own operator. Instructions are built once per *type* and shared by
    every conversation, so a room's facts could not live there even if the
    authority question did not settle it."""
    from friday.domain.models import ApiIssueParams
    from friday.extraction.prompt import build_input, build_instructions

    said = build_input(
        _context("API lỗi", ApiIssueParams, memories=_rows("test.apero: staging"))
    )

    assert "test.apero" in said
    assert "staging" in said
    assert "test.apero" not in build_instructions()


def test_the_rooms_facts_arrive_through_the_memory_section():
    """Criterion: "through the existing memory section builder's channel
    slot, which gains its first caller since it was written"."""
    from friday.domain.models import ApiIssueParams
    from friday.extraction.prompt import build_input

    said = build_input(
        _context("API lỗi", ApiIssueParams, memories=_rows("register: thân mật"))
    )

    assert "<memory>" in said
    assert "[channel" in said


def test_the_field_schema_comes_before_the_room():
    """Stable-first, and which is stabler is not a guess: one `Harness` per
    task type serves every conversation, so the field schema is identical
    across every call that agent makes and the room is not. Putting the room
    first would break the shared prefix for every conversation but one."""
    from friday.domain.models import ApiIssueParams
    from friday.extraction.prompt import build_input

    said = build_input(
        _context("API lỗi", ApiIssueParams, memories=_rows("register: thân mật"))
    )

    assert said.index("Fields:") < said.index("<memory>") < said.index("What they said:")


def test_what_the_operator_wrote_is_labelled_apart_from_what_a_model_did():
    """It was three layers in precedence order — base, derived, overrides —
    and a test that the operator's won. The layers were a file's (ticket 10);
    what survives of the rule is provenance: the operator's rows, here and
    everywhere, under one label and first, a model's under another, and
    nothing dropped — a mutation that lost one kind of row stays red."""
    from friday.domain.models import ApiIssueParams
    from friday.extraction.prompt import build_input

    said = build_input(
        _context(
            "API lỗi",
            ApiIssueParams,
            memories=(
                *_rows("the reelme team is busiest", origin="model"),
                *_rows("company: apero", channel_id="*"),
                *_rows("test.apero: staging"),
            ),
        )
    )

    operator, model = said.split("the operator wrote:")[1].split("remembered:")
    assert "apero" in operator and "staging" in operator
    assert "the reelme team" in model


# --- what we already asked (ticket 05) -----------------------------------


def test_outstanding_questions_reach_the_conversation_slot_not_the_channel():
    """`memory()`'s two slots are the two halves of "what do I already know?"
    — this room, and this exchange. Ticket 01 filled the channel slot with the
    room's facts; a question this task already asked is about the exchange."""
    from friday.domain.models import ApiIssueParams
    from friday.extraction.prompt import build_input

    said = build_input(
        _context("API lỗi", ApiIssueParams, asked=("em gửi anh curl với",))
    )

    assert "[conversation" in said
    assert "em gửi anh curl với" in said
    assert "[channel" not in said


def test_a_task_that_asked_nothing_renders_no_such_content():
    """Absent contributes nothing, not an empty heading — and the prompt of a
    task nobody has asked anything stays what it was."""
    from friday.domain.models import ApiIssueParams
    from friday.extraction.prompt import build_input

    assert build_input(_context("API lỗi", ApiIssueParams, asked=())) == build_input(
        _context("API lỗi", ApiIssueParams)
    )


def test_the_room_and_the_outstanding_questions_are_both_labelled():
    """Both slots at once, each still saying which is which — the property
    `memory()` was written for and the reason it is one section."""
    from friday.domain.models import ApiIssueParams
    from friday.extraction.prompt import build_input

    said = build_input(
        _context(
            "API lỗi",
            ApiIssueParams,
            memories=_rows("test.apero: staging"),
            asked=("còn environment nào em?",),
        )
    )

    assert "[conversation" in said and "[channel" in said
    assert said.index("[conversation") < said.index("[channel"), (
        "the section's own order changed"
    )


#: `test_the_extractor_itself_reads_what_it_already_asked`,
#: `test_the_extractor_itself_reads_domain_kind_memories`,
#: `test_voice_kind_memories_do_not_reach_the_extractor` and
#: `test_the_outstanding_questions_cost_no_model_call` moved to
#: `tests/test_extraction_context.py` in ticket 15 (D26): they tested
#: `Extractor`'s own lookup of the room, the domain memories and the open
#: questions, which no longer exists — `build_full_context` does all four
#: reads now, and `Extractor` holds no store of its own to test here.


def test_an_extractor_whose_harness_answers_a_different_shape_is_refused():
    """Board `every-answer-has-a-shape`, ticket 05. Same argument as
    `register`'s check against `PARAMS`, one level down: a harness that
    declares a different `answers=` produces the wrong type at runtime, in the
    middle of a task, where the only symptom is fields that never fill in.
    Refusing at wiring time costs a restart."""
    from friday.domain.models import AccessRequestParams, ApiIssueParams

    with pytest.raises(ValueError, match="ApiIssueParams"):
        build_extractor(
            params_cls=ApiIssueParams,
            harness=ScriptedHarness(answers=answer_shape(AccessRequestParams)),  # type: ignore[arg-type]
            name="mismatched",
        )


async def test_a_skill_fetch_and_a_correction_both_fit_in_one_extraction():
    """The spec named this as the largest risk of the move (`Further Notes`):
    the extractor runs on one turn plus one, and a correction now spends a turn
    inside the run rather than buying a second run. Whether a correction and a
    skill fetch can both fit was a thing to watch, so it is measured here
    rather than reasoned about.

    Three model calls: reach for a skill, answer wrongly, answer again. All
    three land inside one run.
    """
    from friday.sdk.testing import ScriptedModel, function_call

    from friday.agent.skills import SkillLibrary
    from friday.config import AgentConfig
    from friday.domain.models import ApiIssueParams
    from friday.extraction.answer import answer_shape
    from pathlib import Path

    library = SkillLibrary(Path(__file__).resolve().parents[1] / "skills").load()
    catalogue = list(library.catalogue())
    assert catalogue, "this repo ships skills; without one the harness grants no turns"

    model = ScriptedModel([
        [function_call("search_skills", {"query": "correlation"}, call_id="1")],
        [function_call("answer", {"correlation_id": ["not", "a", "string"]}, call_id="2")],
        [function_call("answer", {"correlation_id": "3f7a1e22"}, call_id="3")],
    ])
    ext = build_extractor(
        params_cls=ApiIssueParams,
        harness=Harness(
            config=AgentConfig(
                name="api_issue_ext", api_key="k",
                base_url="https://example.invalid/v1", model="test-model",
            ),
            instructions="extract",
            answers=answer_shape(ApiIssueParams),
            skills=library,
            model=model,
        ),
        name="budgeted",
    )

    params, _ = await ext.run(_context("api lỗi", ApiIssueParams))

    assert params is not None and params.correlation_id == "3f7a1e22"
    assert len(model.calls) == 3
