"""Two-file backup + WAL + recovery (ticket 09, §12.1).

Friday keeps two SQLite files — the application db and DBOS's workflow system
db — that are one state. A backup that caught one without the other could
restore a task whose workflow is gone; a restore of both together is what keeps
domain and workflow state in step. WAL is on for both so a board read never
blocks a writing workflow. Restart recovery of an in-flight workflow itself is
proven on real DBOS in `test_workflow_port.py::
test_a_crash_resumes_from_the_last_incomplete_step`.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from friday.kernel.ops.backup import Backup, databases, restore


def _make_db(path: Path, value: str) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS t (v TEXT)")
        conn.execute("INSERT INTO t (v) VALUES (?)", (value,))
        conn.commit()
    finally:
        conn.close()


def _rows(path: Path) -> list[str]:
    conn = sqlite3.connect(str(path))
    try:
        return [r[0] for r in conn.execute("SELECT v FROM t")]
    finally:
        conn.close()


def _sources(tmp_path: Path) -> list[str]:
    app, system = tmp_path / "friday.db", tmp_path / "friday.system.db"
    _make_db(app, "app")
    _make_db(system, "workflow")
    return [str(app), str(system)]


async def test_a_backup_copies_both_files(tmp_path):
    sources = _sources(tmp_path)
    backup = Backup(sources=sources, backup_dir=str(tmp_path / "backups"), keep=7)

    made = await backup.run_if_due(now=datetime(2026, 9, 24, tzinfo=UTC))

    assert made
    day = tmp_path / "backups"
    assert _rows(day / "friday.20260924.db") == ["app"]
    assert _rows(day / "friday.system.20260924.db") == ["workflow"]


async def test_it_backs_up_at_most_once_a_day(tmp_path):
    """The date is the guard — safe to call every beat, and once a day across a
    restart, because the file itself is the record of having run."""
    sources = _sources(tmp_path)
    backup = Backup(sources=sources, backup_dir=str(tmp_path / "backups"), keep=7)
    now = datetime(2026, 9, 24, tzinfo=UTC)

    assert await backup.run_if_due(now=now) is True
    assert await backup.run_if_due(now=now) is False  # already done today
    assert len(list((tmp_path / "backups").glob("friday.2026*.db"))) == 1


async def test_retention_keeps_the_newest_days_both_halves(tmp_path):
    sources = _sources(tmp_path)
    backup = Backup(sources=sources, backup_dir=str(tmp_path / "backups"), keep=2)
    for day in (22, 23, 24):
        await backup.run_if_due(now=datetime(2026, 9, day, tzinfo=UTC))

    kept = sorted(p.name for p in (tmp_path / "backups").glob("*.db"))
    # Only the newest two days, and both files of each — never a lone half.
    assert kept == [
        "friday.20260923.db",
        "friday.20260924.db",
        "friday.system.20260923.db",
        "friday.system.20260924.db",
    ]


async def test_keep_zero_turns_the_backup_off(tmp_path):
    backup = Backup(
        sources=_sources(tmp_path), backup_dir=str(tmp_path / "backups"), keep=0
    )

    assert await backup.run_if_due(now=datetime(2026, 9, 24, tzinfo=UTC)) is False
    assert not (tmp_path / "backups").exists()


async def test_a_system_db_not_created_yet_is_skipped(tmp_path):
    """A fresh install has no workflow db until the first run; the app db is
    still backed up rather than the whole backup failing."""
    app = tmp_path / "friday.db"
    _make_db(app, "app")
    sources = [str(app), str(tmp_path / "friday.system.db")]  # system db absent
    backup = Backup(sources=sources, backup_dir=str(tmp_path / "backups"), keep=7)

    assert await backup.run_if_due(now=datetime(2026, 9, 24, tzinfo=UTC)) is True
    day = tmp_path / "backups"
    assert (day / "friday.20260924.db").exists()
    assert not (day / "friday.system.20260924.db").exists()


async def test_restore_brings_both_files_back(tmp_path):
    sources = _sources(tmp_path)
    backup = Backup(sources=sources, backup_dir=str(tmp_path / "backups"), keep=7)
    await backup.run_if_due(now=datetime(2026, 9, 24, tzinfo=UTC))

    # The live files move on, then a restore rewinds both to the backup.
    _make_db(Path(sources[0]), "app-later")
    _make_db(Path(sources[1]), "workflow-later")

    restored = restore(
        "20260924", sources=sources, backup_dir=str(tmp_path / "backups")
    )

    assert set(restored) == set(sources)
    assert _rows(Path(sources[0])) == ["app"]
    assert _rows(Path(sources[1])) == ["workflow"]


async def test_restore_clears_stale_wal_sidecars(tmp_path):
    """The case restore exists for is a crash, which leaves a stale `<db>-wal`
    beside the live file. SQLite would replay it over the just-restored file on
    next open, undoing the restore — so restore deletes the sidecars."""
    sources = _sources(tmp_path)
    backup = Backup(sources=sources, backup_dir=str(tmp_path / "backups"), keep=7)
    await backup.run_if_due(now=datetime(2026, 9, 24, tzinfo=UTC))

    # A crash left WAL/SHM sidecars beside the live application db.
    app = Path(sources[0])
    stale_wal = app.with_name(app.name + "-wal")
    stale_shm = app.with_name(app.name + "-shm")
    stale_wal.write_bytes(b"stale")
    stale_shm.write_bytes(b"stale")

    restore("20260924", sources=sources, backup_dir=str(tmp_path / "backups"))

    assert not stale_wal.exists() and not stale_shm.exists()
    assert _rows(app) == ["app"]


def test_databases_pairs_the_app_and_the_system_file():
    assert databases("/data/friday.db") == ["/data/friday.db", "/data/friday.system.db"]


def test_restore_refuses_a_partial_day(tmp_path):
    """Both halves of a day restore together, or neither: a task without its
    workflow (or the reverse) is the exact corruption two-file backup exists to
    prevent."""
    sources = _sources(tmp_path)
    day = tmp_path / "backups"
    day.mkdir()
    # Only the app half of 2026-09-24 exists.
    _make_db(day / "friday.20260924.db", "app")

    with pytest.raises(FileNotFoundError, match="partial restore"):
        restore("20260924", sources=sources, backup_dir=str(day))


async def test_an_unrelated_file_in_the_dir_is_left_alone(tmp_path):
    sources = _sources(tmp_path)
    day = tmp_path / "backups"
    day.mkdir()
    (day / "notes.txt").write_text("keep me")
    backup = Backup(sources=sources, backup_dir=str(day), keep=1)

    await backup.run_if_due(now=datetime(2026, 9, 24, tzinfo=UTC))

    assert (day / "notes.txt").read_text() == "keep me"


def test_enable_wal_puts_a_file_in_wal_mode(tmp_path):
    """WAL for the DBOS system db (§12.1): the app db sets it in `store/db.py`;
    the adapter sets it on the workflow db before DBOS opens it."""
    from friday.kernel.dag.adapter import _enable_wal

    path = tmp_path / "sub" / "sys.db"  # parent created on the way
    _enable_wal(str(path))

    conn = sqlite3.connect(str(path))
    try:
        (mode,) = conn.execute("PRAGMA journal_mode").fetchone()
    finally:
        conn.close()
    assert mode.lower() == "wal"


async def test_the_application_db_is_wal(tmp_path):
    """The other half of "WAL on both": a file-backed application db is WAL."""
    from friday.store.db import Database

    db = await Database.connect(str(tmp_path / "app.db"), create=True)
    try:
        async with db._sessions() as session:
            from sqlalchemy import text as sql_text

            mode = (await session.execute(sql_text("PRAGMA journal_mode"))).scalar()
    finally:
        await db.close()
    assert str(mode).lower() == "wal"
