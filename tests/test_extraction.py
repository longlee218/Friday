"""Ticket 31 — workflow-owned extraction.

The extraction module is the second half of the seam. It must:
- register an extractor for a task type
- parse a model's output into a Params instance
- return None on any failure (the workflow falls back to structural only)
- run before validate, so hallucinated values are still caught
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
    extractor,
    registered,
)
from friday.validation import Matches


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
    extractor("fake_test_type_31", ext)

    try:
        assert "fake_test_type_31" in registered()
    finally:
        registered().pop("fake_test_type_31", None)


def test_extractor_decorator_rejects_double_registration():
    @dataclass
    class FakeParams:
        x: Optional[str] = None

    class StubHarness:
        async def run(self, *a, **kw):
            return None

    ext = build_extractor(
        params_cls=FakeParams,
        harness=StubHarness(),  # type: ignore[arg-type]
        name="first",
    )
    extractor("duplicate_test_type_31", ext)

    try:
        with pytest.raises(ValueError, match="already registered"):
            extractor(
                "duplicate_test_type_31",
                build_extractor(
                    params_cls=FakeParams,
                    harness=StubHarness(),  # type: ignore[arg-type]
                    name="second",
                ),
            )
    finally:
        registered().pop("duplicate_test_type_31", None)


# --- end-to-end with a scripted model ---------------------------------


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
    extractor("stub_test_31", ext)

    try:
        result = asyncio.run(extract("stub_test_31", "the api is wrong"))
        assert isinstance(result, ParamsWithRules)
        assert result.environment == "production"
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
    extractor("failing_test_31", ext)

    try:
        result = asyncio.run(extract("failing_test_31", "x"))
        assert result is None
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
    extractor("bad_output_test_31", ext)

    try:
        # Output is not JSON and has no key:value lines, so _parse returns {}.
        # The Params constructor then fails with TypeError on the missing
        # required field; Extractor returns None.
        result = asyncio.run(extract("bad_output_test_31", "x"))
        assert result is None
    finally:
        registered().pop("bad_output_test_31", None)


async def test_extract_returns_none_for_unregistered_task_type():
    assert await extract("not_a_real_task_type_31_xyz", "anything") is None


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
    from friday.workflows import PARAMS

    assert set(EXTRACTS) == set(PARAMS)


def test_the_string_null_is_treated_as_absent():
    """The live provider taught us this: the model writes the *string* "null"
    often enough that an unnormalised value is mistaken for a real one, and a
    workflow that believes it has a correlationId never asks for the one it
    needs. The check used to live in triage, which no longer produces values."""
    from friday.extraction import _hygiene
    from friday.models import ApiIssueParams

    cleaned = _hygiene(ApiIssueParams("s", "null", "N/A", "   "))

    assert (cleaned.environment, cleaned.correlation_id, cleaned.curl) == (
        None, None, None
    )


def test_values_are_trimmed():
    from friday.extraction import _hygiene
    from friday.models import DocQuestionParams

    assert _hygiene(DocQuestionParams("  is it optional?  ")).question == (
        "is it optional?"
    )
