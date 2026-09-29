"""findings are not one per key

Board `read-it-the-way-the-operator-does`, ticket 09's review:
`uq_memories_active_key` held a room to one active `finding` per
`service:error_code`, so the second diagnosis of a known fault could not
record what it found. Findings are meant to pile up — several saying the same
thing are the signal a runbook is owed — so the index's predicate now leaves
`finding` out. Nothing is destructive: the new predicate is looser.

Autogenerate emitted `pass` here, because Alembic does not compare an index's
predicate; `tests/test_migrations.py` now does.

Revision ID: f81f63e7d3ce
Revises: 527334fcabc2
Create Date: 2026-09-18 15:38:01.397384

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f81f63e7d3ce"
down_revision: str | Sequence[str] | None = "527334fcabc2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_OLD = "status = 'active' AND deleted_at IS NULL"
_NEW = "status = 'active' AND deleted_at IS NULL AND kind != 'finding'"


def _rebuild(where: str) -> None:
    with op.batch_alter_table("memories", schema=None) as batch_op:
        batch_op.drop_index("uq_memories_active_key")
        batch_op.create_index(
            "uq_memories_active_key",
            ["channel_id", "kind", "key"],
            unique=True,
            sqlite_where=sa.text(where),
        )


def upgrade() -> None:
    """Upgrade schema."""
    _rebuild(_NEW)


def downgrade() -> None:
    """Downgrade schema. Refused by SQLite if a room now holds two active
    findings on one key — the old predicate cannot describe that data."""
    _rebuild(_OLD)
