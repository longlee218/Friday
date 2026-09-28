"""Ticket 04 — one agent at a time, and a write that waits rather than fails.

Two `run_agent` processes would run two outbox loops against one database, so a
reply could be sent twice and the two loops would race on every row. The agent
takes an exclusive lock beside the database at startup; a second one refuses.
And a concurrent read from the board must not knock a write over — SQLite's
`busy_timeout` makes the write wait for the lock rather than raising at once.
"""

from __future__ import annotations

import pytest

from friday.kernel.ops.single_instance import single_instance_lock


def test_a_second_agent_refuses_to_start(tmp_path):
    db = str(tmp_path / "friday.db")
    held = single_instance_lock(db)
    try:
        with pytest.raises(SystemExit) as refused:
            single_instance_lock(db)
        assert "already running" in str(refused.value)
    finally:
        held.close()


def test_the_lock_frees_when_the_holder_lets_go(tmp_path):
    """The lock is the running process, not a stale file: once the first holder
    lets go, a second may start."""
    db = str(tmp_path / "friday.db")
    single_instance_lock(db).close()

    again = single_instance_lock(db)  # must not raise
    again.close()


async def test_busy_timeout_is_set_on_a_connection(tmp_path):
    from friday.store.db import BUSY_TIMEOUT_MS, _engine

    engine = _engine(str(tmp_path / "friday.db"))
    try:
        async with engine.connect() as conn:
            value = (await conn.exec_driver_sql("PRAGMA busy_timeout")).scalar()
        assert value == BUSY_TIMEOUT_MS
        assert BUSY_TIMEOUT_MS > 0
    finally:
        await engine.dispose()
