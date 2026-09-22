"""The regression net for the classifier — `docs/DESIGN.md`'s own words for
what this was always supposed to be. Not exercised by `uv run pytest`: it
calls the configured provider, so it costs money and is scored by hand, when
a prompt changes, against the numbers `evals/README.md` says to expect.

Two pure modules — `scoring.py`, `dataset.py` — hold everything a test can
check without a network. `build_triage_set.py` and `run_triage_eval.py` are
scripts, not tests: the first refreshes `triage.jsonl` from live data, by
hand, when there is new data worth freezing in; the second scores the live
classifier against whatever `triage.jsonl` currently holds.
"""

from __future__ import annotations

from typing import Any

__all__ = ["outputs_or_raise"]


def outputs_or_raise(report: Any) -> list[Any]:
    """Every case's output — but only once every case actually ran.

    `pydantic-evals` catches a task exception, parks the row in
    `report.failures`, and returns the successes. For a scored regression net
    that is the wrong default: a transient provider error on one row would
    quietly shrink the denominator, and accuracy, the confusion matrix, the
    threshold table and the out-of-set count would all be computed over a
    smaller set with nothing saying so. The hand-loop these runners replaced
    aborted loudly instead, and this keeps that: a dropped row is an error, not
    a quietly better-looking score.
    """
    if report.failures:
        failed = "; ".join(
            f"{f.name}: {f.error_message}" for f in report.failures
        )
        raise RuntimeError(
            f"{len(report.failures)} eval case(s) failed to run, so the set "
            f"scored would be smaller than the set given — {failed}"
        )
    return [case.output for case in report.cases]
