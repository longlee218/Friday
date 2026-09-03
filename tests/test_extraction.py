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
        last_error = None

        async def run(self, prompt, *, context=None, calls=None, extra_turns=0):
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
        last_error = "boom"

        async def run(self, prompt, *, context=None, calls=None, extra_turns=0):
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
        async def run(self, prompt, *, context=None, calls=None, extra_turns=0):
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
    from friday.extraction import Clarify, _Capture, _clarify_tool

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
            tools=[_clarify_tool(ApiIssueParams)],
            context_type=_Capture,
            model=ScriptedModel(
                [
                    [
                        function_call(
                            "ask_clarification",
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
    from friday.extraction import _clarify_tool

    for params_cls in (ApiIssueParams, AccessRequestParams, DocQuestionParams):
        schema = _clarify_tool(params_cls).params_json_schema
        enum = set(schema["properties"]["fields"]["items"]["enum"])
        assert enum == set(params_cls.__dataclass_fields__) - {"summary"}


# --- triage classifies; extraction is the only producer ---------------------


def test_every_classifiable_type_has_an_extractor_configured():
    """Triage fills nothing in. A task type whose extractor is missing opens
    with no parameters at all, and the reporter is asked for what they just
    said — so a block per type in `config.yaml` is not optional any more."""
    import os

    from friday.config import load_config
    from friday.extraction import EXTRACTS

    for key in ("TRIAGE_API_KEY", "RESPONDER_API_KEY"):
        os.environ.setdefault(key, "test-key")
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    agents = load_config(repo / "config.yaml").agents

    missing = [t for t in EXTRACTS if f"extractor_{t}" not in agents]
    assert missing == [], f"no extractor configured for {missing}"


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
