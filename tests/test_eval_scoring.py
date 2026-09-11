"""Ticket 06 — scoring a set of triage predictions against expected labels.

Pure and synchronous: nothing here calls a model, so what is under test is
the arithmetic, not the classifier. `evals/run_triage_eval.py` is the only
real caller, and it supplies predictions from a live run.
"""

from __future__ import annotations

from evals.scoring import Prediction, accuracy, confusion_matrix, threshold_table


def test_an_empty_run_has_zero_accuracy_not_a_crash():
    assert accuracy([]) == 0.0


def test_accuracy_is_the_exact_match_rate():
    predictions = [
        Prediction(expected="api_issue", predicted="api_issue", confidence=0.9),
        Prediction(expected="skip", predicted="skip", confidence=0.8),
        Prediction(expected="doc_question", predicted="api_issue", confidence=0.6),
        Prediction(expected="access_request", predicted="access_request", confidence=0.7),
    ]

    assert accuracy(predictions) == 0.75


def test_confusion_matrix_counts_every_expected_predicted_pair():
    predictions = [
        Prediction(expected="api_issue", predicted="api_issue", confidence=0.9),
        Prediction(expected="api_issue", predicted="skip", confidence=0.4),
        Prediction(expected="skip", predicted="skip", confidence=0.95),
    ]

    matrix = confusion_matrix(predictions)

    assert matrix["api_issue"]["api_issue"] == 1
    assert matrix["api_issue"]["skip"] == 1
    assert matrix["skip"]["skip"] == 1


def test_confusion_matrix_gives_a_label_a_row_even_at_zero():
    """A label the classifier never once guessed must not silently vanish
    from the printed table — it is the confusion worth seeing most."""
    predictions = [
        Prediction(expected="api_issue", predicted="api_issue", confidence=0.9),
        Prediction(expected="access_request", predicted="api_issue", confidence=0.5),
    ]

    matrix = confusion_matrix(predictions)

    assert matrix["access_request"]["access_request"] == 0
    assert matrix["api_issue"]["access_request"] == 0


def test_a_label_only_ever_predicted_still_gets_a_column():
    """`needs_human` — the eval set never expects it (`DECISIONS` excludes
    it), but the classifier can still produce it. A matrix built only from
    the expected labels would raise on this row rather than show it."""
    predictions = [
        Prediction(expected="api_issue", predicted="needs_human", confidence=0.0),
    ]

    matrix = confusion_matrix(predictions)

    assert matrix["api_issue"]["needs_human"] == 1


def test_threshold_table_counts_what_confidence_below_it_would_escalate():
    predictions = [
        Prediction(expected="api_issue", predicted="api_issue", confidence=0.9),
        Prediction(expected="api_issue", predicted="api_issue", confidence=0.6),
        Prediction(expected="skip", predicted="skip", confidence=0.3),
    ]

    table = threshold_table(predictions, thresholds=(0.5, 0.7))

    # 0.5: only the 0.3 example falls below it.
    assert table[0.5] == 1
    # 0.7: both 0.6 and 0.3 fall below it.
    assert table[0.7] == 2


def test_a_confidence_exactly_at_the_threshold_is_not_escalated():
    """Matches `TriageRunner._apply`'s own comparison —
    `state = PENDING if confidence >= threshold else NEEDS_HUMAN` — so the
    ceiling itself must count as decided, not escalated. `confidence < threshold`
    and `confidence <= threshold` agree everywhere except exactly here."""
    predictions = [Prediction(expected="api_issue", predicted="api_issue", confidence=0.7)]

    assert threshold_table(predictions, thresholds=(0.7,))[0.7] == 0


def test_threshold_table_does_not_care_whether_the_guess_was_right():
    """A wrongly-classified row that would still be escalated is not a
    contradiction — the confusion matrix answers correctness; this answers
    only what a threshold would have caught before a person read it."""
    predictions = [
        Prediction(expected="api_issue", predicted="skip", confidence=0.4),
    ]

    assert threshold_table(predictions, thresholds=(0.5,))[0.5] == 1
