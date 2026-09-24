"""Ticket 00 — the recall-first rule, on lines alone.

The one guard that matters here: **the decisive line survives.** Every other
assertion in this file is about what the rule is allowed to cut instead.

Its own module rather than a section of `test_api_issue.py`, because ticket
16's coverage test lands here: for each labelled case the operator names the
line they say decided it, and a pure-code test asserts the rule keeps it.
"""

from __future__ import annotations

from plugins.devops.graph.distil import distil, frames


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


def test_a_warning_is_kept_but_is_not_evidence_that_anything_failed():
    """**Measured on production, 2026-09-21.** `backend-reelme-v2` emits
    about seven WARN lines a minute of routine chatter — "Engine returned an
    unmappable node status … skipping node", "No credit cost configured …
    falling back to 15". Nothing is wrong; that is the service working.

    With WARN counting as an error, `has_error` was true in every window this
    service will ever produce, so the one automatic widening could never fire
    for it. The two questions only looked alike: a warning is often the line
    before the failure and is worth *keeping*, and it is not evidence either
    way about whether anything failed.
    """
    warned = distil(log('{"level":"WARN","msg":"falling back to 15"}'))

    assert warned.lines, "still kept — it is often the line before the failure"
    assert not warned.has_error
    assert warned.worth_widening


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


# --- the histogram: counts, not lines (tickets 02 and 03) -------------------


def test_the_error_codes_in_the_window_are_counted_not_quoted():
    """The spec's answer to a noisy window, and the reason `FindRequestLog`
    is capped at twelve lines: "an error-code histogram of ±5 min — counts,
    not lines; the raw window is a median 58 lines, up to 885". The first
    real run kept 61 lines of other requests' errors and called it a
    dossier."""
    lines = log(
        *[f'{{"errorCode":"ERR951"}}' for _ in range(40)],
        *[f'{{"errorCode":"ERR19"}}' for _ in range(3)],
        '{"errorCode":"ERR306","correlationId":"abc"}',
    )

    kept = distil(lines, correlation_id="abc", other_error_cap=2)

    assert kept.histogram == (("ERR951", 40), ("ERR19", 3), ("ERR306", 1))


def test_the_histogram_counts_the_whole_window_not_what_survived():
    """Counting what survived would be counting the cut, which says nothing
    about the window it was cut from."""
    lines = log(*[f'{{"errorCode":"ERR951"}}' for _ in range(40)], "ERROR abc")

    kept = distil(lines, correlation_id="abc", other_error_cap=1)

    assert dict(kept.histogram)["ERR951"] == 40
    assert kept.kept < 40


def test_the_sample_of_other_requests_says_how_many_it_stands_for():
    """"8 of N", not a silent 8. A sample nobody is told is a sample reads as
    everything."""
    lines = log(*[f"ERROR {i}" for i in range(30)], "the request abc")

    kept = distil(lines, correlation_id="abc", other_error_cap=8)

    assert any("8 of 30" in line for line in kept.not_checked)


def test_a_window_with_no_codes_has_no_histogram():
    kept = distil(log("GET /v1/x 200", "ERROR something unstructured"))

    assert kept.histogram == ()
