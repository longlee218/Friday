"""The models and the migrations must not drift apart.

Tests build their schema straight from the models; the running service builds
it from migrations. That is fast and convenient right up until the two stop
describing the same database, at which point every test passes and production
breaks. This is the only thing standing between those two worlds.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import MetaData, create_engine

from friday import schema

ROOT = Path(__file__).resolve().parent.parent


def test_the_models_and_the_migrations_describe_the_same_database(tmp_path):
    path = tmp_path / "fresh.db"
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env={**os.environ, "FRIDAY_DB": str(path)},
        check=True,
        capture_output=True,
    )

    engine = create_engine(f"sqlite:///{path}")
    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection, opts={"render_as_batch": True}
        )
        assert compare_metadata(context, schema.Base.metadata) == []
        _assert_primary_keys_match(connection)


def _assert_primary_keys_match(connection) -> None:
    """`compare_metadata` does not report a changed primary key.

    Verified: swapping the `messages` key from (provider, provider_message_id)
    to provider_message_id alone leaves the diff empty. Alembic will not
    autogenerate that change either, so the guard has to check it directly or
    the one drift that silently breaks deduplication goes unnoticed.
    """
    live = MetaData()
    live.reflect(bind=connection)
    for name, table in schema.Base.metadata.tables.items():
        expected = [c.name for c in table.primary_key.columns]
        actual = [c.name for c in live.tables[name].primary_key.columns]
        assert actual == expected, f"{name} primary key drifted: {actual} != {expected}"


def test_migrating_does_not_switch_the_applications_logging_off(tmp_path):
    """Alembic's own config sets the root logger to WARNING and disables every
    existing logger. From the CLI that is what you want. Called from inside the
    app it silences the agent, which looks exactly like an agent receiving
    nothing — and that is how it was found."""
    import run_agent

    root = logging.getLogger()
    before = (root.level, list(root.handlers))
    logging.basicConfig(level=logging.DEBUG, force=True)
    os.environ["FRIDAY_DB"] = str(tmp_path / "logging.db")
    try:
        run_agent.migrate()

        assert logging.getLogger("friday.inbox").isEnabledFor(logging.DEBUG)
    finally:
        os.environ.pop("FRIDAY_DB", None)
        logging.basicConfig(level=before[0], force=True)
        root.handlers[:] = before[1]


def test_a_migration_that_already_half_applied_can_still_finish(tmp_path):
    """This happened to the live database and stopped the service booting.

    `original_text` was added and made NOT NULL, then the backfill and the
    version stamp did not run — SQLite DDL was outside a transaction, so a
    failure partway left the schema ahead of the stamp. Every subsequent start
    then died on `duplicate column name: original_text`, before anything
    opened the database. `env.py` runs migrations transactionally now, and
    this migration can finish the job it left half done.
    """
    import subprocess
    import sqlite3

    db = tmp_path / "half.db"
    env = {**os.environ, "FRIDAY_DB": str(db)}

    def alembic(*args):
        return subprocess.run(
            ["uv", "run", "alembic", *args],
            cwd=ROOT, env=env, capture_output=True, text=True,
        )

    # The state the live database was in: one revision short, with that
    # revision's column already present and its backfill never run.
    alembic("upgrade", "71a2c91fa3b0")
    with sqlite3.connect(db) as raw:
        raw.execute(
            "INSERT INTO messages (provider, provider_message_id, conversation_id,"
            " author_id, author_name, text, created_at, is_own, channel_id)"
            " VALUES ('discord','1','discord:c','u','someone','api bị lỗi rồi',"
            "'2026-09-01T00:00:00+00:00',0,'c')"
        )
        raw.execute("ALTER TABLE messages ADD COLUMN original_text TEXT")

    done = alembic("upgrade", "head")
    assert done.returncode == 0, done.stderr

    with sqlite3.connect(db) as raw:
        text, original = raw.execute(
            "SELECT text, original_text FROM messages"
        ).fetchone()
        assert original == text, "the backfill that never ran did not run now"
