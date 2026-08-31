"""The models and the migrations must not drift apart.

Tests build their schema straight from the models; the running service builds
it from migrations. That is fast and convenient right up until the two stop
describing the same database, at which point every test passes and production
breaks. This is the only thing standing between those two worlds.
"""

from __future__ import annotations

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
