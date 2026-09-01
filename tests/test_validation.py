"""Ticket 30 — the validation engine and the seam that uses it."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Optional

import pytest

from friday.models import ApiIssueParams
from friday.validation import (
    InSet,
    Matches,
    NonEmpty,
    OneOf,
    Problem,
    validate,
)
from friday.workflows import _problems, _question


# --- the engine --------------------------------------------------------


def test_a_class_with_no_rules_validates_cleanly():
    class Bare:
        pass

    assert validate(Bare()) == []


def test_matches_with_none_passes():
    """None means 'missing', and missing is the missing-check's job, not this one's."""

    assert Matches(pattern=r".+").check(None) is None


def test_matches_with_a_blank_string_passes():
    """An empty or whitespace-only string is 'no value', not a malformed value.

    The structural check reports it as missing; the validator stays silent so
    the operator hears about it once.
    """

    rule = Matches(pattern=r"^[a-f0-9-]{36}$", name="uuid")
    assert rule.check("") is None
    assert rule.check("   ") is None


def test_matches_with_a_string_that_does_not_match():
    message = Matches(pattern=r"^[a-f0-9-]{36}$", name="uuid").check("lol123")
    assert message is not None
    assert "uuid" in message


def test_matches_with_a_string_that_matches():
    assert Matches(pattern=r"^[a-f0-9-]{36}$").check(
        "abcdef01-2345-6789-abcd-ef0123456789"
    ) is None


def test_in_set_rejects_a_value_outside_the_set():
    rule = InSet(values=frozenset({"production", "staging", "dev"}))
    assert rule.check("prodlike") is not None
    assert rule.check("production") is None


def test_in_set_with_blank_string_passes():
    rule = InSet(values=frozenset({"production", "staging", "dev"}))
    assert rule.check("") is None
    assert rule.check("   ") is None


def test_non_empty_rejects_non_string_values():
    """NonEmpty only fires on a present-but-blank situation it should not see
    in practice; the test guards against it raising instead of returning a
    clean problem.
    """

    assert NonEmpty().check(42) is not None


def test_one_of_passes_when_any_named_field_has_a_value():
    @dataclass
    class WithTwo:
        a: Optional[str] = None
        b: Optional[str] = None
        _RULES = {"_a_or_b": OneOf(fields=("a", "b"))}

    assert validate(WithTwo(a="x", b=None)) == []
    assert validate(WithTwo(a=None, b="y")) == []
    assert len(validate(WithTwo(a=None, b=None))) == 1


def test_one_of_with_empty_fields_raises_at_construction():
    """Constructing a rule that always reports is a bug. Catch it early."""

    with pytest.raises(ValueError, match="at least one field"):
        OneOf(fields=())


def test_one_of_treats_blank_strings_as_missing():
    """A blank string in either field is no value, so the rule still reports."""

    @dataclass
    class WithTwo:
        a: Optional[str] = None
        b: Optional[str] = None
        _RULES = {"_a_or_b": OneOf(fields=("a", "b"))}

    assert len(validate(WithTwo(a="", b="   "))) == 1


def test_validate_returns_one_problem_per_failing_field_not_stop_on_first():
    @dataclass
    class WithTwoFields:
        env: Optional[str] = None
        cid: Optional[str] = None

        _RULES = {
            "env": InSet(frozenset({"production", "staging"})),
            "cid": Matches(r"^[a-f0-9-]{36}$", name="uuid"),
        }

    problems = validate(WithTwoFields(env="devlike", cid="lol"))
    assert len(problems) == 2
    assert {p.field for p in problems} == {"env", "cid"}


def test_problem_renders_as_field_when_message_is_empty():
    p = Problem(field="cid")
    assert str(p) == "cid"


def test_problem_renders_as_field_colon_message_when_message_set():
    p = Problem(field="cid", message="does not match 'uuid'")
    assert str(p) == "cid: does not match 'uuid'"


# --- the seam ----------------------------------------------------------


def test_problems_merges_structural_and_semantic():
    """A non-Optional field with a real _RULES value exercises both halves."""

    @dataclass
    class Tight:
        required_id: str
        optional_env: Optional[str] = None
        _RULES = {"required_id": Matches(r"^[a-f0-9-]{36}$", name="uuid")}

    # structural only: a non-Optional field with a None value
    missing_only = _problems(Tight(required_id=None))  # type: ignore[arg-type]
    assert [p.field for p in missing_only] == ["required_id"]
    assert missing_only[0].message == ""

    # semantic only: present but malformed
    wrong_only = _problems(Tight(required_id="not-a-uuid"))
    assert [p.field for p in wrong_only] == ["required_id"]
    assert "uuid" in wrong_only[0].message

    # structural only: blank string is "no value" — the validator skips it,
    # the structural half fires, exactly one problem.
    blank_only = _problems(Tight(required_id=""))
    assert sum(1 for p in blank_only if p.field == "required_id") == 1
    assert blank_only[0].message == ""


def test_a_params_with_no_rules_passes_validation_and_returns_no_problems():
    good = ApiIssueParams(summary="x")
    assert _problems(good) == []


# --- the question ------------------------------------------------------


def test_question_uses_natural_language_for_known_fields():
    q = _question([Problem(field="correlation_id"), Problem(field="environment")])
    assert "correlationId" in q
    assert "environment" in q
    assert q.startswith("Could you")


def test_question_falls_back_to_field_name():
    q = _question([Problem(field="widget")])
    assert "the widget" in q


def test_question_includes_validation_message_when_it_carries_information():
    q = _question([Problem(field="correlation_id", message="must be one of: 1, 2")])
    assert "1, 2" in q


# --- the only call site -------------------------------------------------


def test_validate_is_only_imported_from_one_module():
    """Ticket 30's seam guarantee. The whole point is one call site. A second
    import is a test failure, not a documentation issue.

    Covers three import shapes:
      from friday.validation import ...
      from friday import validation
      import friday.validation
    """
    hits = subprocess.run(
        [
            "grep",
            "-rlE",
            r"\bfrom\s+friday(\.\s*validation\b|\s+import\s+validation\b)|\bimport\s+friday\.validation\b",
            "friday/",
        ],
        capture_output=True,
        text=True,
    ).stdout.split()

    allowed = {"friday/workflows/__init__.py"}
    assert set(hits) <= allowed, f"unexpected importer: {set(hits) - allowed}"


async def test_an_invalid_value_never_reaches_a_planner_body():
    """The acceptance that earns the seam its name. Register a planner that
    records when it runs; pass params whose rule should fail; assert the
    planner never saw the call.

    Uses `_PLANNERS` directly because that is the registry plan() dispatches
    through — testing it is testing the seam itself.
    """
    from friday.validation import Matches
    from friday.workflows import (
        PARAMS,
        _PLANNERS,
        plan,
        plan_by_required_parameters,
        planner,
    )

    @dataclass
    class StrictParams:
        cid: str
        _RULES = {"cid": Matches(r"^[a-f0-9-]{36}$", name="uuid")}

    called = []

    PARAMS["strict_test_type"] = StrictParams

    @planner("strict_test_type")
    def strict_plan(params: StrictParams):
        called.append(params)
        return plan_by_required_parameters("strict_test_type", params)

    try:
        action = await plan(
            "strict_test_type",
            StrictParams(cid="not-a-uuid"),
            agent=None,
            planners=_PLANNERS,
        )
        assert not called, "planner ran with invalid params"
        from friday.workflows import Ask
        assert isinstance(action, Ask)
        assert "uuid" in str(action.text)
    finally:
        _PLANNERS.pop("strict_test_type", None)
        PARAMS.pop("strict_test_type", None)
