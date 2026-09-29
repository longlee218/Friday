"""Ticket 06's review — the single place `Triage` is assembled from config
and a store.

Split out of `TriageRunner.build` so the `core.triage` eval builds the
identical `Triage` production runs, rather than a second copy of "which
examples, which sensitive words" that could silently drift from it — the
same worry ticket 09's review raised about `MemoryScope` and a bare `8`
standing in for `RESULTS`.
"""

from __future__ import annotations

import pytest

from friday.kernel.config import Config, ConfigError, IngestConfig, TierConfig
from friday.kernel.triage import TRIAGE, Triage
from friday.kernel.triage.runner import EXAMPLES, build_triage


class StubDb:
    def __init__(self, confirmed=()):
        self._confirmed = list(confirmed)
        self.limit_asked = None
        self.decisions_asked = None

    async def confirmed_classifications(self, *, limit, decisions):
        self.limit_asked = limit
        self.decisions_asked = decisions
        return self._confirmed


def _config(**tiers) -> Config:
    return Config(
        database_path=":memory:",
        ingest=IngestConfig(watched_channels=frozenset(), mention_types=frozenset()),
        tiers=tiers,
    )


#: The tier triage runs on, whatever it is named this week.
FLASH = TierConfig(
    name=TRIAGE.tier,
    api_key="k",
    base_url="https://example.invalid/v1",
    model="test-model",
)


async def test_it_builds_a_real_triage_wired_with_confirmed_examples():
    db = StubDb(confirmed=[("the api is down", "backend.trace_problem")])

    triage = await build_triage(_config(**{TRIAGE.tier: FLASH}), db=db)

    assert isinstance(triage, Triage)
    assert "the api is down" in triage._run.instructions


async def test_it_asks_for_the_declared_number_of_examples():
    db = StubDb()

    await build_triage(_config(**{TRIAGE.tier: FLASH}), db=db)

    assert db.limit_asked == EXAMPLES


async def test_a_missing_triage_tier_refuses_by_name():
    with pytest.raises(ConfigError, match=f"'triage' runs on tier '{TRIAGE.tier}'"):
        await build_triage(_config(), db=StubDb())


async def test_confirmed_rows_are_read_for_the_registered_actions_only():
    """A row naming a label no longer registered is dropped, not refused
    (board `domains-plug-in`, ticket 02 §5): the store is asked for exactly
    the registered actions plus `skip`."""
    db = StubDb()

    triage = await build_triage(_config(**{TRIAGE.tier: FLASH}), db=db)

    assert set(db.decisions_asked) == {
        "backend.trace_problem",
        "backend.answer_question",
        "ops.request_permission",
        "skip",
    }
    assert "### backend.answer_question" in triage._run.instructions
