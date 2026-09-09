"""Score the live classifier against `evals/triage.jsonl`.

Not a pytest test and not run by the suite: it calls the configured
provider once per row, so it costs money and takes as long as the provider
does — see `evals/README.md` for what a run costs. Run it by hand after a
prompt change, before deciding whether `confidence_threshold` should move:

    uv run python -m evals.run_triage_eval
    FRIDAY_DB=/path/to/db uv run python -m evals.run_triage_eval   # a different db

Builds a real `friday.triage.Triage` through `friday.triage.runner.build_triage`
— the same function `TriageRunner.build` calls, not a second copy of it — so
a difference this run reports is a difference the live classifier would
actually produce, not an artifact of a second copy of the prompt or its
examples.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from friday.config import Config, load_config
from friday.domain.actions import Decided, TriageOutcome
from friday.domain.models import InboundEvent, MentionType
from friday.store.db import Database
from friday.triage import Triage
from friday.triage.runner import build_triage

from evals.dataset import Example, load_jsonl
from evals.scoring import Prediction, accuracy, confusion_matrix, threshold_table

__all__ = ["DATASET", "main", "report", "run"]

DATASET = Path(__file__).parent / "triage.jsonl"


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
    return Prediction(expected=example.expected, predicted="needs_human", confidence=0.0)


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
    No `record=` sink and no `spent=` ledger: this reports confidence, it
    does not act on a budget or keep an audit trail of its own.
    """
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
) -> list[Prediction]:
    """Score every row in `dataset_path`.

    `triage` is for tests — a `Triage` built on `ScriptedModel`, so wiring
    can be checked with no network and no live database. The real caller,
    `main`, leaves it `None` and gets one built from configuration against
    the configured provider.
    """
    if triage is None:
        triage = await _build_triage(config or load_config())
    predictions = []
    for index, example in enumerate(load_jsonl(dataset_path)):
        outcome = await triage.decide(_event(example, index), turn=_turn(example, index))
        predictions.append(_to_prediction(example, outcome))
    return predictions


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

    return "\n".join(lines)


async def main() -> None:
    load_dotenv()  # `run_agent.py`'s own first step — secrets from .env, never config.yaml.
    predictions = await run()
    print(report(predictions))


if __name__ == "__main__":
    asyncio.run(main())
