"""`core.triage`: the live classifier scored against `evals/datasets/triage/`.

Each case is a turn (`cases.py`) whose folder is the label it expects. The
task runs the real `Triage` — built by `build_triage`, the function
`TriageRunner.build` calls, so a difference a run reports is one production
would produce — and each result is a `Prediction`. The report prints the
accuracy, the confusion matrix, the threshold table, the out-of-set count and
every case that came back wrong, by file.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from friday.kernel.domain.messages import InboundEvent, MentionType
from friday.kernel.domain.tasks import SKIP
from friday.kernel.domain.triage import Decided, TriageOutcome
from friday.kernel.evals.cases import load_turn_cases
from friday.kernel.evals.triage_scoring import (
    Prediction,
    accuracy,
    confusion_matrix,
    out_of_set,
    threshold_table,
)
from friday.sdk.eval import EvalCase, EvalSpec

if TYPE_CHECKING:
    from friday.kernel.config import Config
    from friday.kernel.triage import Triage
    from friday.store.db import Database

__all__ = [
    "DATASET",
    "TRIAGE_EVAL",
    "build_live_triage",
    "build_task",
    "decisions",
    "live_db",
    "report",
    "to_prediction",
    "unfit",
]

#: The set, at the repository root: data, not code. Not in git — it is real
#: channel traffic, with tokens and customer data (`.gitignore`, like
#: `data/cases/`).
DATASET = Path(__file__).resolve().parents[3] / "evals" / "datasets" / "triage"


def to_prediction(case: EvalCase, outcome: TriageOutcome) -> Prediction:
    """What a case scored as. `needs_human` for anything triage did not
    decide — the prefilter held it, or no classification came back — at
    confidence `0.0`, `TriageRunner._record`'s own mapping."""
    if isinstance(outcome, Decided):
        return Prediction(case.expected, outcome.type, outcome.confidence)
    return Prediction(case.expected, "needs_human", 0.0, out_of_set=outcome.out_of_set)


def _turn(case: EvalCase) -> list[InboundEvent]:
    """The case's messages as the raw events a turn is, one second apart.
    `channel_id="eval"` matches no configured channel."""
    now = datetime.now(UTC)
    return [
        InboundEvent(
            provider="eval",
            provider_message_id=f"eval-{case.name}-{i}",
            channel_id="eval",
            thread_id=None,
            author_id="eval-reporter",
            author_name="eval",
            text=text,
            created_at=now + timedelta(seconds=i),
            mention_type=MentionType.DIRECT,
            is_own=own,
        )
        for i, (text, own) in enumerate(case.inputs)
    ]


def build_task(triage: Triage) -> Callable[[EvalCase], Awaitable[Prediction]]:
    """The task: one case through `triage.decide`, the way `TriageRunner`
    calls it — the identity event carries the whole turn joined, and the turn
    goes in as its raw messages."""

    async def classify(case: EvalCase) -> Prediction:
        turn = _turn(case)
        said = "\n".join(m.text for m in turn)
        identity = InboundEvent(
            provider="eval",
            provider_message_id=f"eval-{case.name}",
            channel_id="eval",
            thread_id=None,
            author_id="eval-reporter",
            author_name="eval",
            text=said,
            created_at=turn[0].created_at,
            mention_type=MentionType.DIRECT,
        )
        return to_prediction(case, await triage.decide(identity, turn=turn))

    return classify


@asynccontextmanager
async def live_db(config: Config) -> AsyncIterator[Database]:
    """The database an eval reads: `FRIDAY_DB`, else `config.database_path`."""
    from friday.store.db import Database

    db = await Database.connect(os.environ.get("FRIDAY_DB") or config.database_path)
    try:
        yield db
    finally:
        await db.close()


async def build_live_triage(config: Config) -> Triage:
    """The production `Triage` (`runner.build_triage`), its examples read once
    from the live database, the way `TriageRunner.build` reads them."""
    from friday.kernel.triage.runner import build_triage

    async with live_db(config) as db:
        return await build_triage(config, db=db)


def report(results: Sequence[tuple[EvalCase, Prediction]]) -> str:
    predictions = [p for _, p in results]
    lines = [f"{len(predictions)} examples, accuracy {accuracy(predictions):.1%}", ""]

    matrix = confusion_matrix(predictions)
    labels = sorted(matrix)
    width = max(16, *(len(label) + 2 for label in labels)) if labels else 16
    lines.append("confusion (rows: expected, columns: predicted)")
    lines.append("".ljust(width) + "".join(label.ljust(width) for label in labels))
    for expected in labels:
        row = "".join(str(matrix[expected][guess]).ljust(width) for guess in labels)
        lines.append(expected.ljust(width) + row)

    lines.append("")
    lines.append("confidence below threshold -> escalated to a human:")
    for threshold, count in threshold_table(predictions).items():
        lines.append(f"  {threshold}: {count}/{len(predictions)}")

    # Always printed, including as zero: a line that appears only when it is
    # non-zero is a line whose absence means both "none" and "not measured".
    invented = out_of_set(predictions)
    lines.append("")
    lines.append(
        f"decisions outside the closed set: {invented}/{len(predictions)}"
        + ("  <- the model named a type that does not exist" if invented else "")
    )

    wrong = [(c, p) for c, p in results if p.predicted != p.expected]
    lines.append("")
    lines.append(f"wrong: {len(wrong)}/{len(predictions)}")
    lines += [
        f"  {c.name}: predicted {p.predicted} ({p.confidence:.2f})" for c, p in wrong
    ]
    return "\n".join(lines)


def decisions(actions: Iterable) -> tuple[str, ...]:
    """Every label a case may expect: the registered actions plus `skip`."""
    return (*(a.name for a in actions), SKIP)


def unfit(cases: Sequence[EvalCase], decisions: Iterable[str]) -> list[str]:
    """Why this set is not fit to score a classifier against — empty if it is.

    A guard over the local set (run by the suite when it exists): a decision with no
    case, a set that never shows the classifier a turn of more than one
    message, and one turn that appears twice. Whether a case sits near a
    boundary is the operator's judgement, not something code can decide.
    """
    problems = [
        f"no case expects {missing!r} — triage may reach it, so nothing is scoring it"
        for missing in sorted(set(decisions) - {c.expected for c in cases})
    ]
    if not any(len(c.inputs) > 1 for c in cases):
        problems.append(
            "no case carries a turn of more than one message — a turn is what "
            "the classifier is shown in a real channel"
        )
    seen: dict[tuple, str] = {}
    for case in cases:
        first = seen.setdefault(tuple(case.inputs), case.name)
        if first != case.name:
            problems.append(
                f"{case.name} repeats {first} — a duplicate doubles its own weight"
            )
    return problems


def _correct(case: EvalCase, prediction: Prediction) -> bool:
    return prediction.predicted == case.expected


TRIAGE_EVAL = EvalSpec(
    name="core.triage",
    description="Triage's label for each case in evals/datasets/triage/, "
    "against the folder it sits in.",
    cases=lambda: load_turn_cases(DATASET),
    checks={"correct": _correct},
    report=report,
)
