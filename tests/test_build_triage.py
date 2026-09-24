"""Ticket 06's review — the single place `Triage` is assembled from config
and a store.

Split out of `TriageRunner.build` so `evals/run_triage_eval.py` builds the
identical `Triage` production runs, rather than a second copy of "which
examples, which sensitive words" that could silently drift from it — the
same worry ticket 09's review raised about `MemoryScope` and a bare `8`
standing in for `RESULTS`.
"""

from __future__ import annotations

import pytest

from friday.config import AgentConfig, Config, IngestConfig
from friday.triage import Triage
from friday.triage.runner import build_triage


class StubDb:
    def __init__(self, confirmed=()):
        self._confirmed = list(confirmed)
        self.limit_asked = None

    async def confirmed_classifications(self, *, limit):
        self.limit_asked = limit
        return self._confirmed


def _config(**agents) -> Config:
    return Config(
        database_path=":memory:",
        ingest=IngestConfig(watched_channels=frozenset(), mention_types=frozenset()),
        agents=agents,
    )


def _triage_agent(**options) -> AgentConfig:
    return AgentConfig(
        name="triage",
        api_key="k",
        base_url="https://example.invalid/v1",
        model="test-model",
        options=options,
    )


async def test_it_builds_a_real_triage_wired_with_confirmed_examples():
    db = StubDb(confirmed=[("the api is down", "devops.api_issue")])
    config = _config(triage=_triage_agent())

    triage = await build_triage(config, db=db)

    assert isinstance(triage, Triage)
    assert "the api is down" in triage._run.instructions


async def test_it_asks_for_the_configured_number_of_examples():
    db = StubDb()
    config = _config(triage=_triage_agent(examples=3))

    await build_triage(config, db=db)

    assert db.limit_asked == 3


async def test_a_missing_triage_agent_is_a_clear_exit_not_a_key_error():
    with pytest.raises(SystemExit, match="No 'triage' agent"):
        await build_triage(_config(), db=StubDb())
