"""Scoring a set of triage predictions against their expected labels.

Pure and synchronous: nothing here calls a model or touches the database, so
it is unit-tested without either. `run_triage_eval.py` is the only real
caller, and it supplies predictions from a live run against
`evals/triage.jsonl`.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "Prediction", "accuracy", "confusion_matrix", "out_of_set", "threshold_table",
]

#: The values `run_triage_eval.report` prints a row for. `0.5` is not a
#: number anyone has settled on; it is the low end of the range
#: `confidence_threshold` might plausibly move to.
DEFAULT_THRESHOLDS: tuple[float, ...] = (0.5, 0.6, 0.7, 0.8, 0.9)


@dataclass(frozen=True, slots=True)
class Prediction:
    """One eval row, scored.

    `predicted` is `"needs_human"` for anything triage declined to decide —
    held by the sensitive-word prefilter, or no classification produced at
    all — because that is the actual outcome the row produced, not a task
    type it guessed and got wrong. `confidence` is `0.0` for those, matching
    `TriageRunner._record`'s own mapping.
    """

    expected: str
    predicted: str
    confidence: float
    #: The model named something outside the closed decision set (D20). Such a
    #: row is `needs_human` like any other triage declined to decide — that is
    #: the outcome it produced, and the mention reaches a person either way —
    #: so without this the matrix cannot tell it from a provider outage. The
    #: two lead different places: one says a prompt or a model is wrong, the
    #: other says the network was.
    out_of_set: bool = False


def accuracy(predictions: list[Prediction]) -> float:
    """Exact-match rate. `0.0` on an empty list rather than a
    `ZeroDivisionError` — a run against a set nobody has built yet should say
    so as a number, not a traceback."""
    if not predictions:
        return 0.0
    correct = sum(1 for p in predictions if p.predicted == p.expected)
    return correct / len(predictions)


def confusion_matrix(predictions: list[Prediction]) -> dict[str, dict[str, int]]:
    """`{expected: {predicted: count}}`.

    Every label that appears anywhere — as an expected value or a guess —
    gets a full row and a full column, even where the count is zero, so a
    label the classifier never once guesses does not silently disappear from
    the printed table. That is the confusion most worth seeing.
    """
    labels = sorted(
        {p.expected for p in predictions} | {p.predicted for p in predictions}
    )
    matrix = {expected: {guess: 0 for guess in labels} for expected in labels}
    for prediction in predictions:
        matrix[prediction.expected][prediction.predicted] += 1
    return matrix


def threshold_table(
    predictions: list[Prediction],
    thresholds: tuple[float, ...] = DEFAULT_THRESHOLDS,
) -> dict[float, int]:
    """How many rows each threshold would escalate to a human — confidence
    below it, the same comparison `TriageRunner._apply` runs in production.

    Silent about whether the guess was *right*: a wrongly-classified row that
    a threshold would still escalate is not a contradiction, it is a
    threshold doing its job on a low-confidence answer that happened to be
    wrong. `confusion_matrix` is where correctness is answered.
    """
    return {
        threshold: sum(1 for p in predictions if p.confidence < threshold)
        for threshold in thresholds
    }


def out_of_set(predictions: list[Prediction]) -> int:
    """How many rows the model answered with a decision that does not exist.

    Its own number rather than a share of the accuracy figure, because the two
    failures it separates are not the same size of problem. A classifier that
    picks the wrong real type is wrong about this message; one that invents a
    type has stopped answering the question it was asked, and before board
    `every-answer-has-a-shape` it could open a task the pool then discovered
    had no graph.

    Zero is a reading, not a silence: a report that mentions this only when it
    is non-zero is a report whose absence means both "none" and "not measured".
    """
    return sum(1 for p in predictions if p.out_of_set)
