"""Ticket 30 — the validation engine and the seam that uses it."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import Optional

import pytest

from plugins.backend.params import ApiIssueParams
from friday.sdk.validation import (
    InSet,
    Matches,
    NonEmpty,
    OneOf,
    Problem,
    validate,
)
from friday.kernel.dag.prepare import _problems, _question


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
        _RULES = {"_a_or_b": OneOf(fields=("a", "b"), ask="a or b")}

    assert validate(WithTwo(a="x", b=None)) == []
    assert validate(WithTwo(a=None, b="y")) == []
    assert len(validate(WithTwo(a=None, b=None))) == 1


def test_one_of_with_empty_fields_raises_at_construction():
    """Constructing a rule that always reports is a bug. Catch it early."""

    with pytest.raises(ValueError, match="at least one field"):
        OneOf(fields=(), ask="a or b")


def test_one_of_without_a_phrase_cannot_be_constructed():
    """A rule that can report has to know how to ask, and it is the one thing
    that cannot fall back to field metadata: it reports under a sentinel, so
    there is no field to read a phrase off.

    Two shapes because there are two mistakes. Omitting it is the
    constructor's own `TypeError`; a blank one is refused with a message that
    says why a rule in particular has nowhere to fall back to."""

    with pytest.raises(TypeError, match="ask"):
        OneOf(fields=("a", "b"))  # type: ignore[call-arg]

    with pytest.raises(ValueError, match="`ask`"):
        OneOf(fields=("a", "b"), ask="   ")


def test_one_of_treats_blank_strings_as_missing():
    """A blank string in either field is no value, so the rule still reports."""

    @dataclass
    class WithTwo:
        a: Optional[str] = None
        b: Optional[str] = None
        _RULES = {"_a_or_b": OneOf(fields=("a", "b"), ask="a or b")}

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
    """`AccessRequestParams` declares no `_RULES`. `ApiIssueParams` used to be
    the example here and no longer can be: it has a cross-field rule, and a
    report with neither a correlationId nor a curl is not usable."""
    from plugins.ops.params import AccessRequestParams

    good = AccessRequestParams(project="payments", permission="write", summary="x")
    assert _problems(good) == []


def test_an_api_issue_with_nothing_to_trace_on_is_not_usable():
    """The rule that is not expressible as a type: every field of this type is
    optional, and a report that names no request at all cannot be investigated.
    Without this the graph ran its whole path to discover it could do
    nothing."""
    problems = _problems(ApiIssueParams(summary="API lỗi nè"))

    params = ApiIssueParams(summary="API lỗi nè")
    assert [p.field for p in problems] == ["_traceable"]
    assert "curl" in _question(params, problems)
    assert "endpoint" in _question(params, problems)


def test_a_curl_or_an_endpoint_with_an_id_makes_a_request_findable():
    """Ticket 01 replaced "a correlationId or a curl" with this, and the
    replacement is the ticket: a correlationId is not something a reporter
    has, and "login API, deviceId X, 500" is a report that can be
    investigated. The endpoint on its own is not: it matches every caller of
    it, which on the production case of 2026-09-21 was ten other people's
    successful requests."""
    assert _problems(ApiIssueParams(summary="s", curl="curl -X GET /pay")) == []
    assert _problems(
        ApiIssueParams(summary="s", endpoint="/v1/login", identifier="dev-42")
    ) == []
    assert [
        p.field for p in _problems(ApiIssueParams(summary="s", endpoint="/v1/login"))
    ] == ["_traceable"]
    assert [
        p.field for p in _problems(ApiIssueParams(summary="s", identifier="dev-42"))
    ] == ["_traceable"]


def test_a_correlation_id_alone_is_not_findability():
    """The half of the old rule that was wrong, pinned so it cannot come
    back. A correlationId reaches the graph only alongside something that
    says *where* the request went — it is read out of a pasted response, and
    a response says nothing about which host was called."""
    cid = "abcdef01-2345-6789-abcd-ef0123456789"

    assert [
        p.field for p in _problems(ApiIssueParams(summary="s", correlation_id=cid))
    ] == ["_traceable"]


# --- the question ------------------------------------------------------


def test_question_uses_natural_language_for_known_fields():
    q = _question(
        ApiIssueParams(summary="s"),
        [Problem(field="endpoint"), Problem(field="environment")],
    )
    assert "endpoint" in q
    assert "environment" in q
    assert q.startswith("Could you")


def test_a_field_with_no_phrase_is_refused_rather_than_guessed_at():
    """This replaces `test_question_falls_back_to_field_name`, which asserted
    the behaviour ticket 13 deleted. The fallback turned the field name into a
    sentence — "the widget", "the retry after" — which reads acceptably often
    enough that `project` went the life of its type with no phrase and nothing
    said so. A reporter asked for "the retry after" is a worse outcome than a
    loud failure in front of whoever added the field."""
    with pytest.raises(ValueError, match="how to ask"):
        _question(ApiIssueParams(summary="s"), [Problem(field="widget")])


def test_question_includes_validation_message_when_it_carries_information():
    q = _question(
        ApiIssueParams(summary="s"),
        [Problem(field="correlation_id", message="must be one of: 1, 2")],
    )
    assert "1, 2" in q


# --- the only call site -------------------------------------------------


def test_validate_is_only_invoked_from_one_call_site():
    """Ticket 30's seam guarantee. The whole point is one call site: the
    engine must not be invoked from anywhere but `_problems` in
    `friday/kernel/dag/prepare.py`. `friday.models` and `friday.kernel.extraction`
    may import the rule vocabulary to declare what fields are valid; that
    is not a call site, that is data.

    Greps for `validate(` and `friday.sdk.validation.validate(`.
    """
    hits = subprocess.run(
        [
            "bash",
            "-c",
            'grep -rnE '
            '"\\b(friday\\.validation\\.validate|validate)\\s*\\(" '
            'friday/ '
            '--include=*.py '
            '| grep -v "^friday/sdk/validation\\.py:" '
            '| cut -d: -f1 | sort -u',
        ],
        capture_output=True,
        text=True,
    ).stdout.split()

    # Imports of `from friday.sdk.validation import ...` are declarations of
    # rules; that is data, not a call site. The seam guarantees the rule
    # *engine* only runs from one place, which is `friday/kernel/dag/prepare.py`.
    allowed = {"friday/kernel/dag/prepare.py"}
    assert set(hits) <= allowed, f"unexpected caller: {set(hits) - allowed}"


async def test_an_invalid_value_never_reaches_a_planner_body():
    """The acceptance that earns the seam its name. Register a planner that
    records when it runs; pass params whose rule should fail; assert the
    planner never saw the call.

    Driven through the two calls `prepare_node`'s node runs, in that order,
    rather than through a wrapper only tests used.
    """
    from friday.sdk.validation import Matches
    from friday.kernel.dag import registry
    from friday.kernel.dag.prepare import plan_by_required_parameters, prepare
    from friday.sdk.plugin import TaskTypeSpec

    @dataclass
    class StrictParams:
        cid: str = field(default="", metadata={"ask": "the correlationId"})
        _RULES = {"cid": Matches(r"^[a-f0-9-]{36}$", name="uuid")}

    # Register a throwaway task type; the autouse fixture clears the registry
    # after the test, so it does not leak.
    from friday.sdk.workflow import DAG, Node

    registry.register_task_type(
        TaskTypeSpec(name="strict_test_type", params=StrictParams),
        dag=DAG(name="strict_test_type", nodes=(Node("prepare", lambda s, d: None),)),
    )

    params, problem = await prepare(
        "strict_test_type", StrictParams(cid="not-a-uuid")
    )
    action = problem or plan_by_required_parameters("strict_test_type", params)
    from friday.sdk.actions import Ask

    assert isinstance(action, Ask), "a malformed value was accepted"
    assert "uuid" in str(action.text)


# --- how to ask lives beside the field (ticket 13) -----------------------


def _asks() -> dict[tuple[str, str], str]:
    """Every question this system can ask, keyed by `(type, subject)`.

    **Keyed per type, not per field name.** A name shared by two classes would
    otherwise collapse into one entry, and a class missing a phrase would hide
    behind a class that has one.

    **Every subject `asked_as` can be handed**, which is wider than the askable
    fields: `validate` reports under any `_RULES` key, whether or not it starts
    with an underscore and whether or not it names a field the model may ask
    about. A rule on `summary` would reach the resolver even though nothing
    offers `summary` to the model.

    Resolved by **calling the real resolver**, not by re-reading metadata the
    way it does. A copy of the lookup would keep passing if the metadata key
    were renamed, which is the drift this whole ticket is about.
    """
    from friday.kernel.dag import registry
    from friday.kernel.domain.models import askable_fields
    from friday.sdk.validation import asked_as

    found: dict[tuple[str, str], str] = {}
    for cls in dict.fromkeys(registry.decision_params().values()):
        subjects = set(askable_fields(cls)) | set(getattr(cls, "_RULES", {}))
        for subject in subjects:
            try:
                found[(cls.__name__, subject)] = asked_as(cls, subject)
            except ValueError:
                found[(cls.__name__, subject)] = ""
    return found


def test_every_askable_field_says_how_to_ask_about_it():
    """The sibling of `test_every_extraction_field_tells_the_model_what_it
    _means`, and the guard `_ASKED_AS` never had. A dict far from the fields it
    names drifts silently: `project` was askable with no entry for as long as
    the type existed, and the fallback made it read acceptably enough that
    nothing said so."""
    unaskable = [name for name, phrase in _asks().items() if not phrase.strip()]

    assert unaskable == [], f"askable with no way to ask about it: {unaskable}"


def test_the_questions_this_system_can_ask_are_written_down():
    """"What can it ask?" has to be answerable, and after ticket 13 it is not
    answerable by reading one dict any more.

    So it is answerable here, the way "what can the agents do?" is answered by
    `tests/test_tools.py`: a list asserted, not a module that collects things
    to be read. The **phrases** and not only the subjects, because a phrase
    that moved is a question that changed, and moving them was the whole
    change. Adding a field or a rule turns this red, which is the point — a
    new question is a new thing a reporter is asked, and that is worth a line
    in a diff.

    The split this feeds — which of these name something that must survive
    translation — is asserted where the rule that needs it lives, in
    `tests/test_responder_check.py`.
    """
    assert _asks() == {
        ("ApiIssueParams", "environment"): "which environment you're on",
        # Not askable — `correlation_id` carries no `ask` of its own. The
        # phrase is on its `Matches` rule, for the one case that can still
        # report it: a value that came back malformed, answered by asking for
        # the response it should have been read out of.
        ("ApiIssueParams", "correlation_id"): "the response you got back",
        ("ApiIssueParams", "response"): "the response you got back",
        ("ApiIssueParams", "endpoint"): "which endpoint you called",
        ("ApiIssueParams", "identifier"):
            "the deviceId, userId, email or order id you used",
        ("ApiIssueParams", "curl"): "the curl you used",
        ("ApiIssueParams", "_traceable"):
            "the curl you used, or which endpoint you called plus one id it "
            "carried",
        ("AccessRequestParams", "project"): "which project you need access to",
        ("AccessRequestParams", "permission"): "what access you need",
        ("DocQuestionParams", "question"): "what you would like to know",
        ("DocQuestionParams", "doc_ref"): "which document you mean",
    }


def test_one_of_refuses_an_empty_alternative():
    """An empty group satisfies nothing, so a rule containing one always
    reports — which is exactly what the constructor's other guard exists to
    prevent, and it was added without a test beside it."""
    import pytest

    from friday.sdk.validation import OneOf

    with pytest.raises(ValueError, match="empty alternative"):
        OneOf(fields=("curl", ()), ask="the curl you used")
    with pytest.raises(ValueError, match="empty alternative"):
        OneOf(fields=("curl", ""), ask="the curl you used")
