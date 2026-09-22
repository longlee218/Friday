"""Ticket 14 — scoring a diagnosis against what the operator said was true.

The suite checks that code does what it was told to. Nothing in it checks
whether the *diagnosis* got better or worse, and three changes to the
distillation landed on 2026-09-21 alone with one person reading one cause
and nodding. Under architecture v3.3 that gap stops being tolerable: the
model drives its own reads, so the same case may be investigated two ways.

These are tests of the scoring, which is pure. Whether the model is right is
what the eval measures; whether the eval measures it is what these do.
"""

from __future__ import annotations

from evals.api_issue import Scored, report, score

CASE = {
    "id": "prod-onboarding-400",
    "cause_mentions": ["categoryId", "ValidationPipe"],
    "conclusive": True,
}


def said(**kw) -> dict:
    return {"cause": "", "conclusive": True, "refs": ["L1"], **kw}


def test_a_cause_carrying_every_token_passes():
    got = score(CASE, said(
        cause="Client gửi categoryId rỗng; ValidationPipe từ chối request."
    ))

    assert got.cause_found
    assert got.missing == ()


def test_a_cause_missing_one_token_says_which():
    """The useful half of a failure. "0/1" says a change made things worse
    and nothing about where to look."""
    got = score(CASE, said(cause="ValidationPipe từ chối vì body không hợp lệ"))

    assert not got.cause_found
    assert got.missing == ("categoryId",)


def test_matching_ignores_case_because_a_cause_is_prose():
    got = score(CASE, said(cause="categoryid rỗng, validationpipe chặn"))

    assert got.cause_found


def test_a_case_nobody_labelled_does_not_score_full_marks():
    """The one way a growing set could get quieter as it gets weaker: an
    unlabelled case would otherwise pass for any answer at all."""
    got = score({"id": "x"}, said(cause="anything at all"))

    assert not got.cause_found


def test_a_voided_answer_is_a_third_outcome_not_a_cautious_one():
    """The refs gate and the alternatives gate can throw an answer away.
    Collapsing that into `conclusive: false` would make a run that refused
    to answer look like one that answered carefully."""
    got = score(CASE, None)

    assert got.said_conclusive is None
    assert not got.grounded
    assert not got.cause_found


def test_conclusive_is_compared_against_the_operators_judgement():
    """Whether the evidence really settled it is a fact about their system.
    A model that calls everything conclusive scores worse, which is the
    point."""
    over = score(CASE, said(cause="categoryId ValidationPipe", conclusive=True))
    under = score(CASE, said(cause="categoryId ValidationPipe", conclusive=False))

    assert over.said_conclusive == over.expected_conclusive
    assert under.said_conclusive != under.expected_conclusive


def test_refs_are_counted_because_a_cause_with_none_was_not_built_on_evidence():
    assert score(CASE, said(cause="x", refs=["L1", "L7"])).refs == 2
    assert score(CASE, said(cause="x", refs=[])).refs == 0


# --- the report ------------------------------------------------------------


def _scored(**kw) -> Scored:
    return Scored(
        case="c", cause_found=True, missing=(), expected_conclusive=True,
        said_conclusive=True, refs=1, grounded=True, **kw
    )


def test_a_small_set_says_it_is_not_a_score():
    """Three out of three is not a score, and a report that prints a fraction
    without saying so invites it to be read as one."""
    said_so = report([_scored()])

    assert "not a score" in said_so


def test_a_set_large_enough_stops_apologising():
    assert "not a score" not in report([_scored() for _ in range(10)])


def test_every_row_is_printed_beside_the_totals():
    """A single figure over a set this small moves by ten per cent when one
    case changes its mind, and nobody can act on it without the rows."""
    said_so = report([
        _scored(),
        Scored(case="other", cause_found=False, missing=("categoryId",),
               expected_conclusive=True, said_conclusive=True, refs=0,
               grounded=True),
    ])

    assert "MISS" in said_so and "other" in said_so
    assert "missing: categoryId" in said_so


def test_an_empty_set_says_so_rather_than_dividing_by_zero():
    assert "empty" in report([])


# --- the set itself --------------------------------------------------------


def test_an_unlabelled_case_is_named_rather_than_skipped(tmp_path):
    """A case in the directory with no labels scores zero for the cause,
    which reads as the model failing when it is the set that is unfinished."""
    from evals.run_api_issue_eval import unlabelled

    assert unlabelled([{"id": "a", "cause_mentions": ["x"]}, {"id": "b"}]) == ["b"]


def test_the_cases_are_read_in_a_stable_order(tmp_path):
    """Two runs of the same set have to be comparable, and a directory
    listing is not ordered."""
    import json

    from evals.run_api_issue_eval import cases

    for name in ("c.json", "a.json", "b.json"):
        (tmp_path / name).write_text(json.dumps({"id": name[0]}))

    assert [c["id"] for c in cases(tmp_path)] == ["a", "b", "c"]


def test_the_labels_are_a_declared_part_of_a_case():
    """`replay_case` refuses a key nothing reads, so the labels have to be
    named there — and the refusal is what stopped a mistyped
    `correlation_id` from silently undoing a fix."""
    from replay_case import CASE_KEYS

    assert {"cause_mentions", "conclusive", "decisive", "cause"} <= CASE_KEYS
