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
