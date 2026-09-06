"""Ticket 06 — building `evals/triage.jsonl` from live data.

Offline: `db` is the same in-memory fixture every other store test uses, so
this covers the wiring (confirmed verdicts read, few-shot examples excluded,
the file written) without a real database or a network call.
"""

from __future__ import annotations

import json

from conftest import make_event

from friday.config import Config, IngestConfig

from evals.build_triage_set import SEED, build_and_write


async def _confirmed(db, message_id: str, kind: str, text: str) -> None:
    """A message triage classified and the operator then marked right —
    `tests/test_verdicts.py`'s own fixture shape, since that is exactly what
    `confirmed_classifications` reads."""
    event = make_event(message_id=message_id, text=text)
    await db.record_message(event)
    await db.mark_triaged(
        event, decision={"type": kind, "confidence": 0.9, "params": {}}
    )
    await db.record_verdict(
        provider="fake", provider_message_id=message_id, mark="right", by="operator"
    )


async def test_it_writes_confirmed_verdicts_plus_the_seed(db, tmp_path):
    await _confirmed(db, "m1", "api_issue", "the api is 500ing")
    out = tmp_path / "triage.jsonl"

    count = await build_and_write(db, _config(), out=out)

    texts = {row["text"]: row["expected"] for row in _read_jsonl(out)}
    assert texts["the api is 500ing"] == "api_issue"
    assert count == len(SEED) + 1


async def test_an_example_used_as_a_few_shot_is_not_in_the_frozen_set(db, tmp_path):
    seed_text, seed_type = SEED[0]
    out = tmp_path / "triage.jsonl"

    await build_and_write(
        db, _config(triage_examples=((seed_text, seed_type),)), out=out
    )

    texts = {row["text"] for row in _read_jsonl(out)}
    assert seed_text not in texts


def _config(*, triage_examples=()) -> Config:
    return Config(
        database_path=":memory:",
        ingest=IngestConfig(watched_channels=frozenset(), mention_types=frozenset()),
        triage_examples=triage_examples,
    )


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
