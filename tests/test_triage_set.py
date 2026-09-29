"""Growing `evals/datasets/triage/` from ✅-marked verdicts (`add_confirmed`).

Offline: `db` is the in-memory store every other store test uses. What is
checked is that it only ever adds — the folder is the operator's — and that
nothing the prompt already shows the model becomes a case.
"""

from __future__ import annotations

import logging

from conftest import make_event

from friday.kernel.config import Config, IngestConfig
from friday.kernel.evals.cases import load_turn_cases
from friday.kernel.evals.triage_set import add_confirmed


async def _confirmed(db, message_id: str, kind: str, text: str) -> None:
    """A message triage classified and the operator marked right."""
    event = make_event(message_id=message_id, text=text)
    await db.record_message(event)
    await db.mark_triaged(
        event, decision={"type": kind, "confidence": 0.9, "params": {}}
    )
    await db.record_verdict(
        provider="fake", provider_message_id=message_id, mark="right", by="operator"
    )


def _config() -> Config:
    return Config(
        database_path=":memory:",
        ingest=IngestConfig(watched_channels=frozenset(), mention_types=frozenset()),
    )


async def test_a_marked_verdict_becomes_a_case_in_its_label_folder(db, tmp_path):
    await _confirmed(db, "m1", "backend.trace_problem", "the api is 500ing")

    (written,) = await add_confirmed(db, _config(), root=tmp_path, excluded=[])

    assert written.parent.name == "backend.trace_problem"
    (case,) = load_turn_cases(tmp_path)
    assert case.inputs == (("the api is 500ing", False),)


async def test_a_hand_written_case_is_never_touched(db, tmp_path):
    folder = tmp_path / "backend.trace_problem"
    folder.mkdir()
    mine = folder / "001-mine.md"
    mine.write_text(
        "---\nexpected_task: backend.trace_problem\n---\nthe api is 500ing\n"
    )
    await _confirmed(db, "m1", "backend.trace_problem", "the api is 500ing")

    assert await add_confirmed(db, _config(), root=tmp_path, excluded=[]) == []
    assert mine.read_text().endswith("the api is 500ing\n")


async def test_an_example_the_prompt_shows_is_not_scored(db, tmp_path):
    await _confirmed(db, "m1", "skip", "ok a, e hiểu rồi ạ")

    assert (
        await add_confirmed(
            db, _config(), root=tmp_path, excluded=[("ok a, e hiểu rồi ạ", "skip")]
        )
        == []
    )


async def test_by_default_the_prompts_own_examples_are_left_out(db, tmp_path):
    from friday.kernel.plugin_host import registered_actions
    from friday.kernel.triage.prompt import declared_examples

    text, label = declared_examples(registered_actions())[0]
    await _confirmed(db, "m1", label, text)

    assert await add_confirmed(db, _config(), root=tmp_path) == []


async def test_a_set_left_unfit_says_so(db, tmp_path, caplog):
    await _confirmed(db, "m1", "backend.trace_problem", "the api is 500ing")

    with caplog.at_level(logging.WARNING, logger="friday.kernel.evals.triage_set"):
        await add_confirmed(db, _config(), root=tmp_path, excluded=[])

    warned = "\n".join(r.getMessage() for r in caplog.records)
    assert "not fit to score" in warned and "'skip'" in warned
