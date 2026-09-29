"""Ticket 06 — building `evals/triage.jsonl` from live data.

Offline: `db` is the same in-memory fixture every other store test uses, so
this covers the wiring (confirmed verdicts read, few-shot examples excluded,
the file written) without a real database or a network call.
"""

from __future__ import annotations

import json

from conftest import make_event

from friday.kernel.config import Config, IngestConfig

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
    await _confirmed(db, "m1", "backend.trace_problem", "the api is 500ing")
    out = tmp_path / "triage.jsonl"

    frozen = await build_and_write(db, _config(), out=out)

    texts = {row["text"]: row["expected"] for row in _read_jsonl(out)}
    assert texts["the api is 500ing"] == "backend.trace_problem"
    assert len(frozen) == len(SEED) + 1


async def test_an_example_used_as_a_few_shot_is_not_in_the_frozen_set(db, tmp_path):
    seed_text, seed_type = SEED[0]
    out = tmp_path / "triage.jsonl"

    await build_and_write(db, _config(), out=out, excluded=[(seed_text, seed_type)])

    texts = {row["text"] for row in _read_jsonl(out)}
    assert seed_text not in texts


def _config() -> Config:
    return Config(
        database_path=":memory:",
        ingest=IngestConfig(watched_channels=frozenset(), mention_types=frozenset()),
    )


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# --- board `every-answer-has-a-shape`, ticket 02 ----------------------------


async def test_a_marked_verdict_replaces_the_seed_row_that_stood_in_for_it(db, tmp_path):
    """D19: the seed is kept only where nothing real yet says the same thing.
    The seed exists to give the set something to score on day one, and the
    whole point of a refresh is that it stops being the only thing there."""
    seed_text, _ = SEED[0]
    await _confirmed(db, "m1", "skip", seed_text)
    out = tmp_path / "triage.jsonl"

    await build_and_write(db, _config(), out=out)

    texts = {row["text"]: row["expected"] for row in _read_jsonl(out)}
    assert texts[seed_text] == "skip", "the seed overwrote the operator's own mark"


async def test_a_refresh_that_loses_a_decision_says_so_out_loud(db, tmp_path, caplog):
    """The moment coverage is actually lost is the moment somebody refreshes,
    and a refresh that drops every `skip` row leaves an accuracy figure that
    looks perfectly healthy. The suite's guard over the committed file catches
    it eventually; this catches it while the operator is still standing there.
    """
    import logging

    out = tmp_path / "triage.jsonl"
    every_seed_row = tuple((text, kind) for text, kind, *_ in SEED)

    with caplog.at_level(logging.WARNING, logger="evals.build_triage_set"):
        await build_and_write(db, _config(), out=out, excluded=list(every_seed_row))

    warned = "\n".join(r.getMessage() for r in caplog.records)
    assert "not fit to score" in warned
    assert "skip" in warned


async def test_a_declared_example_is_not_in_the_frozen_set(db, tmp_path):
    """By default the excluded rows are the declared ones: each action's
    `Recognition.examples` and the core's `skip` examples."""
    from evals.build_triage_set import declared_examples

    declared = declared_examples(_config())
    text, label = declared[0]
    await _confirmed(db, "m1", label, text)
    out = tmp_path / "triage.jsonl"

    await build_and_write(db, _config(), out=out)

    assert text not in {row["text"] for row in _read_jsonl(out)}
