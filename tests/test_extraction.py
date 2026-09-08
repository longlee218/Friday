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

from friday.agent.harness import Refused
from dataclasses import dataclass
from typing import Optional

from friday.extraction import (
    _parse,
    build_extractor,
    extract,
    registered,
)
from friday.domain.validation import Matches


# --- parse helpers ----------------------------------------------------


def test_parse_handles_json_output():
    assert _parse('{"environment": "production"}') == {"environment": "production"}


def test_parse_handles_key_value_lines():
    assert _parse("environment: production\ncurl: https://example.com") == {
        "environment": "production",
        "curl": "https://example.com",
    }


def test_parse_treats_null_values_as_absent():
    assert _parse("environment: null\ncurl: none") == {}


def test_parse_strips_quotes():
    assert _parse('environment: "production"') == {"environment": "production"}


# --- registration -----------------------------------------------------


def test_extractor_decorator_registers_under_task_type():
    @dataclass
    class FakeParams:
        environment: Optional[str] = None

    class StubHarness:
        #: What a real `Harness` with no skill library has. A stub with
        #: fewer attributes than the type it stands in for passes and is
        #: describing itself.
        tool_turns = 0

        async def run(self, *a, **kw):
            return None

    ext = build_extractor(
        params_cls=FakeParams,
        harness=StubHarness(),  # type: ignore[arg-type]
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
        environment: Optional[str] = None

    class StubResult:
        final_output = '{"environment": "production"}'

    class StubHarness:
        tool_turns = 0

        last_error = None

        async def run(self, prompt, *, context=None, extra_turns=0,
                      task_id=None, node=None):
            return StubResult()

    ext = build_extractor(
        params_cls=ParamsWithRules, harness=StubHarness(), name="stub"  # type: ignore[arg-type]
    )
    _install("stub_test_31", ext)

    try:
        params, clarify = asyncio.run(extract("stub_test_31", "the api is wrong"))
        assert isinstance(params, ParamsWithRules)
        assert params.environment == "production"
        assert clarify is None
    finally:
        registered().pop("stub_test_31", None)


def test_an_extractor_returns_none_when_harness_fails():
    class FailingHarness:
        tool_turns = 0

        last_error = "boom"
        #: The model was asked and could not answer, which is not the same as
        #: not asking it — see `Refused`. This is the first of the two.
        refusal = None

        async def run(self, prompt, *, context=None, extra_turns=0,
                      task_id=None, node=None):
            return None

    @dataclass
    class Params:
        environment: Optional[str] = None

    ext = build_extractor(
        params_cls=Params, harness=FailingHarness(), name="fail"  # type: ignore[arg-type]
    )
    _install("failing_test_31", ext)

    try:
        params, clarify = asyncio.run(extract("failing_test_31", "x"))
        assert params is None
        assert clarify is None
    finally:
        registered().pop("failing_test_31", None)


def test_an_extractor_returns_none_when_output_does_not_parse():
    class StubResult:
        final_output = "not even close to JSON"

    class StubHarness:
        tool_turns = 0

        async def run(self, prompt, *, context=None, extra_turns=0,
                      task_id=None, node=None):
            return StubResult()

    @dataclass
    class StrictParams:
        required_id: str  # not Optional - missing raises TypeError

    ext = build_extractor(
        params_cls=StrictParams, harness=StubHarness(), name="bad"  # type: ignore[arg-type]
    )
    _install("bad_output_test_31", ext)

    try:
        # Output is not JSON and has no key:value lines, so _parse returns {}.
        # The Params constructor then fails with TypeError on the missing
        # required field; Extractor returns None.
        params, clarify = asyncio.run(extract("bad_output_test_31", "x"))
        assert params is None
        assert clarify is None
    finally:
        registered().pop("bad_output_test_31", None)


async def test_extract_returns_none_for_unregistered_task_type():
    assert await extract("not_a_real_task_type_31_xyz", "anything") == (None, None)


# --- ask_clarification --------------------------------------------------


async def test_the_extractor_can_ask_for_specific_fields_it_read_it_needs():
    """The scripted-model seam: the model calls `ask_clarification` instead
    of writing field text. `Extractor.run` surfaces it as a `Clarify` —
    intent, not words: which of the type's own fields, and why."""
    from agents.testing import ScriptedModel, assistant_message, function_call

    from friday.agent.harness import Harness
    from friday.config import AgentConfig
    from friday.domain.models import ApiIssueParams
    from friday.extraction.clarify import Clarify, FieldsCapture
    from friday.tools.ask_for_fields import ask_for_fields_tool

    config = AgentConfig(
        name="api_issue_ext",
        api_key="k",
        base_url="https://example.invalid/v1",
        model="test-model",
    )
    ext = build_extractor(
        params_cls=ApiIssueParams,
        harness=Harness(
            config=config,
            instructions="extract",
            tools=[ask_for_fields_tool(ApiIssueParams)],
            context_type=FieldsCapture,
            model=ScriptedModel(
                [
                    [
                        function_call(
                            "ask_for_fields",
                            {
                                "fields": ["correlation_id"],
                                "because": "no id or curl anywhere in the report",
                            },
                            call_id="1",
                        )
                    ],
                    [assistant_message("{}")],
                ]
            ),
        ),
        name="api_issue_ext",
    )
    _install("clarify_test_31", ext)

    try:
        params, clarify = await extract("clarify_test_31", "the api is broken")
        assert clarify == Clarify(
            fields=("correlation_id",), because="no id or curl anywhere in the report"
        )
    finally:
        registered().pop("clarify_test_31", None)


def test_ask_clarification_cannot_name_a_field_that_does_not_exist():
    """The closed enum is the enforcement — `fields` is generated from the
    type's own dataclass fields, `summary` (model-authored) excluded, so the
    schema itself is what stops the model asking about something that is
    not there or that it writes itself."""
    from friday.domain.models import AccessRequestParams, ApiIssueParams, DocQuestionParams
    from friday.tools.ask_for_fields import ask_for_fields_tool

    for params_cls in (ApiIssueParams, AccessRequestParams, DocQuestionParams):
        schema = ask_for_fields_tool(params_cls).params_json_schema
        enum = set(schema["properties"]["fields"]["items"]["enum"])
        assert enum == set(params_cls.__dataclass_fields__) - {"summary"}


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
    from friday.extraction import EXTRACTS

    for key in ("TRIAGE_API_KEY", "RESPONDER_API_KEY"):
        os.environ.setdefault(key, "test-key")
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    agents = load_config(repo / "config.yaml").agents

    assert "extractor" in agents, "no extractor configured at all"
    assert EXTRACTS, "nothing to extract for"
    stale = [name for name in agents if name.startswith("extractor_")]
    assert stale == [], f"per-type extractor blocks are gone; found {stale}"


def test_extracts_covers_every_task_type_that_opens_a_task():
    """`PARAMS` is the set of types that become tasks. Any of them without an
    entry here has nothing filling its fields."""
    from friday.extraction import EXTRACTS
    from friday.domain.models import PARAMS

    assert set(EXTRACTS) == set(PARAMS)


def test_the_string_null_is_treated_as_absent():
    """The live provider taught us this: the model writes the *string* "null"
    often enough that an unnormalised value is mistaken for a real one, and a
    workflow that believes it has a correlationId never asks for the one it
    needs. The check used to live in triage, which no longer produces values."""
    from friday.extraction import _hygiene
    from friday.domain.models import ApiIssueParams

    cleaned = _hygiene(ApiIssueParams("s", "null", "N/A", "   "))

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

    from friday.domain.models import PARAMS

    undocumented = [
        f"{cls.__name__}.{f.name}"
        for cls in set(PARAMS.values())
        for f in dataclass_fields(cls)
        if not (f.metadata or {}).get("doc")
    ]

    assert undocumented == [], f"fields the extractor cannot explain: {undocumented}"


def test_the_doc_reaches_the_extractors_prompt():
    from friday.domain.models import ApiIssueParams
    from friday.extraction.prompt import build_input

    prompt = build_input("API lỗi", ApiIssueParams)

    assert "copied exactly" in prompt
    assert "- summary: summary" not in prompt


async def test_a_model_that_could_not_answer_is_not_a_refusal():
    """The two look identical from here — no result either way — and they are
    told apart by which of them we caused. A model that failed is worth asking
    the reporter about; a call we declined to make is not."""
    from dataclasses import dataclass as _dataclass
    from typing import Optional as _Optional

    from friday.extraction import build_extractor

    class Refuses:
        tool_turns = 0

        last_error = "over budget"
        refusal = "an-agent has spent 999 of its 10 tokens today"

        async def run(self, prompt, *, context=None, extra_turns=0,
                      task_id=None, node=None):
            return None

    @_dataclass
    class Params:
        environment: _Optional[str] = None

    ext = build_extractor(params_cls=Params, harness=Refuses(), name="refuses")

    with pytest.raises(Refused, match="tokens today"):
        await ext.run("anything")


# --- the room reaches the extractor (ticket 01) ---------------------------


def _room(**overrides):
    from friday.memory.channel_context import ChannelContext

    return ChannelContext(
        channel_id="watched", base={}, derived={}, overrides=overrides
    )


def test_a_room_with_no_context_file_leaves_the_prompt_exactly_as_it_was():
    """The tracer bullet must not change the prompt of a room nobody has
    written anything about, and "not much" is not the same as "not at all":
    every extractor in every unconfigured install shares this prefix."""
    from friday.domain.models import ApiIssueParams
    from friday.extraction.prompt import build_input

    assert build_input("API lỗi", ApiIssueParams, room=None) == build_input(
        "API lỗi", ApiIssueParams
    )


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
        "API lỗi", ApiIssueParams, room=_room(**{"test.apero": "staging"})
    )

    assert "test.apero" in said
    assert "staging" in said
    assert "test.apero" not in build_instructions()


def test_the_rooms_facts_arrive_through_the_memory_section():
    """Criterion: "through the existing memory section builder's channel
    slot, which gains its first caller since it was written"."""
    from friday.domain.models import ApiIssueParams
    from friday.extraction.prompt import build_input

    said = build_input("API lỗi", ApiIssueParams, room=_room(register="thân mật"))

    assert "<memory>" in said
    assert "[channel" in said


def test_the_field_schema_comes_before_the_room():
    """Stable-first, and which is stabler is not a guess: one `Harness` per
    task type serves every conversation, so the field schema is identical
    across every call that agent makes and the room is not. Putting the room
    first would break the shared prefix for every conversation but one."""
    from friday.domain.models import ApiIssueParams
    from friday.extraction.prompt import build_input

    said = build_input("API lỗi", ApiIssueParams, room=_room(register="thân mật"))

    assert said.index("Fields:") < said.index("<memory>") < said.index("What they said:")


def test_an_override_the_operator_wrote_wins_over_a_derived_summary():
    """The three layers in the precedence order they already have. A rebuild
    rewrites `derived` and must never change what an operator typed — which is
    what makes writing a fact by hand the producer this board may not ship
    without."""
    from friday.domain.models import ApiIssueParams
    from friday.extraction.prompt import build_input
    from friday.memory.channel_context import ChannelContext

    room = ChannelContext(
        channel_id="watched",
        base={"env": "from base"},
        derived={"env": "from the summariser"},
        overrides={"env": "from the operator"},
    )
    said = build_input("API lỗi", ApiIssueParams, room=room)

    assert "from the operator" in said
    assert "from the summariser" not in said
    assert "from base" not in said


def test_the_extractor_itself_looks_the_room_up_from_the_channel_it_is_given():
    """The guard the end-to-end test could not be: that one's stub built the
    prompt itself, so `Extractor.run`'s own lookup — store plus `channel_id`
    to room to prompt — had no test at all, and a mutation that made the
    extractor ignore its store passed the whole suite.

    This is the point of the ticket, so it is asserted at the seam that does
    it rather than beside it."""
    from friday.memory.channel_context import ChannelContext

    seen: list[str] = []

    class _Store:
        def context(self, channel_id):
            return (
                ChannelContext(
                    channel_id=channel_id,
                    base={},
                    derived={},
                    overrides={"test.apero": "staging"},
                )
                if channel_id == "watched"
                else None
            )

    class StubResult:
        final_output = '{"environment": "staging"}'

    class StubHarness:
        tool_turns = 0
        last_error = None

        async def run(self, prompt, *, context=None, extra_turns=0,
                      task_id=None, node=None):
            seen.append(prompt)
            return StubResult()

    @dataclass
    class Fields:
        environment: Optional[str] = None

    ext = build_extractor(
        params_cls=Fields,
        harness=StubHarness(),  # type: ignore[arg-type]
        name="stub",
        context=_Store(),
    )
    _install("stub_room", ext)
    try:
        asyncio.run(extract("stub_room", "API lỗi", channel_id="watched"))
        asyncio.run(extract("stub_room", "API lỗi", channel_id="somewhere-else"))
    finally:
        registered().pop("stub_room", None)

    with_room, without = seen
    assert "staging" in with_room, "the extractor did not read its own store"
    assert "[channel" in with_room
    assert "staging" not in without, "a room leaked into another channel"
    assert "[channel" not in without


def test_a_layer_the_operator_did_not_override_still_reaches_the_prompt():
    """All three layers, merged. Asserting only that overrides *win* left a
    mutation passing that dropped `base` and `derived` entirely — the
    summariser's own output would have reached nobody, which is the half of
    the context layer ticket 06 exists to fill."""
    from friday.domain.models import ApiIssueParams
    from friday.extraction.prompt import build_input
    from friday.memory.channel_context import ChannelContext

    said = build_input(
        "API lỗi",
        ApiIssueParams,
        room=ChannelContext(
            channel_id="watched",
            base={"company": "apero"},
            derived={"busiest": "the reelme team"},
            overrides={"test.apero": "staging"},
        ),
    )

    assert "apero" in said, "the shared base layer never reached the prompt"
    assert "the reelme team" in said, "the summariser's own layer was dropped"
    assert "staging" in said
