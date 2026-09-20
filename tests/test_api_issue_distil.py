"""Ticket 00 — the recall-first rule, on lines alone.

The one guard that matters here: **the decisive line survives.** Every other
assertion in this file is about what the rule is allowed to cut instead.

Its own module rather than a section of `test_api_issue.py`, because ticket
16's coverage test lands here: for each labelled case the operator names the
line they say decided it, and a pure-code test asserts the rule keeps it.
"""

from __future__ import annotations

from friday.dag.api_issue.distil import distil, frames


def log(*lines: str) -> list[str]:
    return list(lines)


# --- what is never cut ------------------------------------------------------


def test_a_line_carrying_the_correlation_id_survives():
    lines = log(*[f"noise {i}" for i in range(500)], "req abc-123 failed")

    kept = distil(lines, correlation_id="abc-123", max_lines=10)

    assert "req abc-123 failed" in kept.lines


def test_every_error_line_survives_even_without_a_correlation_id():
    """D2's other half: a report with no id is still investigable, and the
    loud lines are what it is investigated from."""
    lines = log(*[f"info {i}" for i in range(300)], "ERROR boom", *[f"info {i}" for i in range(300)])

    kept = distil(lines, max_lines=20)

    assert "ERROR boom" in kept.lines


def test_the_cap_drops_surroundings_and_never_a_decisive_line():
    """The asymmetry this whole rule exists for: a long dossier costs tokens,
    a dossier missing the line costs a wrong cause asserted in the operator's
    name."""
    lines = log(*[f"ERROR {i}" for i in range(50)])

    kept = distil(lines, max_lines=10)

    assert kept.kept == 50
    assert kept.not_checked  # and it says so rather than pretending it fit


def test_surroundings_are_kept_up_to_the_context_lines():
    lines = log("a", "b", "ERROR c", "d", "e", "f")

    kept = distil(lines, context_lines=1)

    assert kept.lines == ("b", "ERROR c", "d")


def test_a_quiet_dossier_is_worth_widening_and_a_loud_one_is_not():
    """Spec: one automatic widening of the window when a dossier has neither
    an ERROR line nor a stack."""
    assert distil(log("GET /v1/x 200", "GET /v1/y 200")).worth_widening
    assert not distil(log("ERROR /v1/x")).worth_widening
    assert not distil(log("at handler (/app/src/orders.ts:12:3)")).worth_widening


def test_matching_identifiers_stand_in_for_a_correlation_id():
    """D2: a curl, *or* an endpoint plus one identifier the log line carries.
    Case 1 of ticket 00 is exactly this shape — a curl and no correlationId."""
    lines = log("GET /v1/health 200", "POST /v1/pod/orders/init 500")

    kept = distil(lines, matching=("/v1/pod/orders/init",), context_lines=0)

    assert kept.lines == ("POST /v1/pod/orders/init 500",)


# --- what it noticed --------------------------------------------------------


def test_total_counts_what_the_source_offered_not_what_survived():
    kept = distil(log(*[f"line {i}" for i in range(100)], "ERROR x"), context_lines=0)

    assert kept.total == 101
    assert kept.kept == 1


def test_frames_are_read_out_of_the_lines_that_survived():
    kept = distil(log("at handler (/app/src/orders.ts:12:3)", "ERROR x"))

    assert frames(kept) == [("/app/src/orders.ts", 12)]


def test_json_log_lines_match_by_substring_rather_than_being_parsed():
    """Production is JSON and dev is plain text. A rule that has to know
    which it is reading has two failure modes instead of one."""
    lines = log('{"level":"error","correlationId":"abc-123","msg":"boom"}')

    kept = distil(lines, correlation_id="abc-123")

    assert kept.lines == tuple(lines)
    assert kept.has_error is True
