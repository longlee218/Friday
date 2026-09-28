"""Score the live classifier against `evals/triage.jsonl`.

Not a pytest test and not run by the suite: it calls the configured
provider once per row, so it costs money and takes as long as the provider
does — see `evals/README.md` for what a run costs. Run it by hand after a
prompt change, before deciding whether `confidence_threshold` should move:

    uv run python -m evals.run_triage_eval
    FRIDAY_DB=/path/to/db uv run python -m evals.run_triage_eval   # a different db

Builds a real `friday.kernel.triage.Triage` through `friday.kernel.triage.runner.build_triage`
— the same function `TriageRunner.build` calls, not a second copy of it — so
a difference this run reports is a difference the live classifier would
actually produce, not an artifact of a second copy of the prompt or its
examples.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import Evaluator, EvaluatorContext

from friday.kernel.config import Config, load_config
from friday.kernel.domain.triage import Decided, TriageOutcome
from friday.kernel.domain.models import InboundEvent, MentionType
from friday.store.db import Database
from friday.kernel.triage import Triage
from friday.kernel.triage.runner import build_triage

from evals import outputs_or_raise
from evals.dataset import Example, load_jsonl
from evals.scoring import (
    Prediction,
    accuracy,
    confusion_matrix,
    out_of_set,
    threshold_table,
)

__all__ = ["DATASET", "main", "report", "run"]

DATASET = Path(__file__).parent / "triage.jsonl"


@dataclass
class _Row:
    """One dataset row as a `pydantic-evals` `Case` input: the frozen example
    plus its position, which `_event`/`_turn` need for the unique message ids a
    turn's messages are correlated by."""

    index: int
    example: Example


@dataclass
class Correct(Evaluator):
    """The framework's per-case view: 1.0 when the classifier named the label
    the row expects. It is exact-match like `accuracy` in `scoring.py`, and it
    exists so `pydantic-evals`' own report (and Logfire) show pass/fail per row;
    the aggregate domain metrics stay in `scoring.py`."""

    def evaluate(self, ctx: EvaluatorContext[_Row, Prediction]) -> float:
        return 1.0 if ctx.output.predicted == ctx.output.expected else 0.0


def _to_prediction(example: Example, outcome: TriageOutcome) -> Prediction:
    """What a dataset row scored as, whatever triage did with it.

    `needs_human` rather than a guessed type for anything triage did not
    decide — the prefilter holding a sensitive word, or no classification at
    all — because that is the outcome the row actually produced. Confidence
    `0.0` matches `TriageRunner._record`'s own mapping for the same case.
    """
    if isinstance(outcome, Decided):
        return Prediction(
            expected=example.expected,
            predicted=outcome.type,
            confidence=outcome.confidence,
        )
    return Prediction(
        expected=example.expected,
        predicted="needs_human",
        confidence=0.0,
        # D20's own number. Still `needs_human` in the matrix — that is the
        # outcome the row produced, and the mention reaches a person either
        # way — but counted separately beside it, because "the model invented
        # a type" and "the provider was down" are different failures reported
        # as one without this.
        out_of_set=outcome.out_of_set,
    )


def _event(example: Example, index: int) -> InboundEvent:
    """The identity event — what `triage.decide`'s first argument is, for the
    sensitive-word check and the `message_id` recording is correlated by.

    Its own `text` is the whole turn joined, the way `TriageRunner._triage_one`
    joins it before either check runs — a row with a real `turn` still needs
    one string that carries everything said, and this is that string, not the
    turn rendered as itself (that is `_turn`, below).

    `channel_id="eval"` reaches no configured whitelist and could not be
    confused with a real one.
    """
    joined = "\n".join(text for text, _ in example.turn) if example.turn else example.text
    return InboundEvent(
        provider="eval",
        provider_message_id=f"eval-{index}",
        channel_id="eval",
        thread_id=None,
        author_id="eval-reporter",
        author_name="eval",
        text=joined,
        created_at=datetime.now(timezone.utc),
        mention_type=MentionType.DIRECT,
    )


def _turn(example: Example, index: int) -> list[InboundEvent]:
    """The turn's own messages, raw — one per `(text, is_own)` pair.

    Empty for the sixteen rows that held one string before ticket 09:
    `Triage.decide` falls back to rendering the identity event alone when
    `turn` is empty, which is the exact shape those rows always exercised —
    so passing this for every row, not only the new ones, changes nothing
    for them and is what makes the new rows measure what triage is actually
    shown rather than a second, parallel code path nothing else exercises.
    """
    return [
        InboundEvent(
            provider="eval",
            provider_message_id=f"eval-{index}-{i}",
            channel_id="eval",
            thread_id=None,
            author_id="eval-reporter",
            author_name="eval",
            text=text,
            created_at=datetime.now(timezone.utc) + timedelta(seconds=i),
            mention_type=MentionType.DIRECT,
            is_own=is_own,
        )
        for i, (text, is_own) in enumerate(example.turn)
    ]


async def _build_triage(config: Config) -> Triage:
    """`build_triage`, against whichever database `FRIDAY_DB` names — or
    `config.database_path` — closed once the `Triage` it returns no longer
    needs it (examples are read once, at build time, same as production).
    No `record=` sink: this reports confidence, it does not keep an audit
    trail of its own.
    """
    # Fill the task-type registry the way the composition root does (ticket 11):
    # triage's closed set is built from it, so it must be populated before the
    # classifier is assembled. No servers/db needed — the eval scores triage,
    # which reads only the registered task types, not their graphs' sources.
    from friday.kernel.dag.router import register_dags

    register_dags(config, servers={})

    db_path = os.environ.get("FRIDAY_DB") or config.database_path
    db = await Database.connect(db_path)
    try:
        return await build_triage(config, db=db)
    finally:
        await db.close()


async def run(
    dataset_path: Path = DATASET,
    *,
    triage: Triage | None = None,
    config: Config | None = None,
    progress: bool = False,
) -> list[Prediction]:
    """Score every row in `dataset_path`, through a `pydantic-evals` `Dataset`.

    The framework owns the plumbing — running each `Case`, the per-case report,
    a Logfire path if configured; the task calls the live `Triage` and returns a
    `Prediction`, and the aggregate domain metrics stay in `report` below.

    `triage` is for tests — a `Triage` built on `ScriptedModel`, so wiring can be
    checked with no network and no live database. **Sequential
    (`max_concurrency=1`)**: a scripted model replays by call order, and the
    triage harness serialises at `_one_run` anyway, so parallelism would only
    make the run non-deterministic for nothing.
    """
    if triage is None:
        triage = await _build_triage(config or load_config())

    async def classify(row: _Row) -> Prediction:
        outcome = await triage.decide(
            _event(row.example, row.index), turn=_turn(row.example, row.index)
        )
        return _to_prediction(row.example, outcome)

    dataset = Dataset[_Row, Prediction, None](
        name="triage",
        cases=[
            Case(name=f"eval-{i}", inputs=_Row(i, example), expected_output=None)
            for i, example in enumerate(load_jsonl(dataset_path))
        ],
        evaluators=[Correct()],
    )
    report_ = await dataset.evaluate(classify, max_concurrency=1, progress=progress)
    # `outputs_or_raise`, not `[c.output for c in cases]`: a row whose task
    # raised is dropped by the framework, and scoring a quietly smaller set is
    # the one thing a regression net must not do. Order-independent otherwise —
    # every metric below is a count or a rate over the set.
    return outputs_or_raise(report_)


def report(predictions: list[Prediction]) -> str:
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
        + (
            "  <- the model named a type that does not exist"
            if invented
            else ""
        )
    )

    return "\n".join(lines)


async def main() -> None:
    load_dotenv()  # `run_agent.py`'s own first step — secrets from .env, never config.yaml.
    predictions = await run(progress=True)
    print(report(predictions))


if __name__ == "__main__":
    asyncio.run(main())
