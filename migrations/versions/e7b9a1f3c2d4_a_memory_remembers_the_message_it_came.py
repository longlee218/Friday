"""a memory remembers the message it came from

Ticket 11 of `.scratch/a-monitor-on-the-whole-path/`: the Rooms screen
distinguishes messages that opened a task (`is_task`) from messages that
produced a memory (`is_enrichment`). Both markers need a join back to
`memories`, and the join needs `memories.source_message_id`.

The column is nullable and indexed because:
- Older rows were written before the message could be threaded into the
  memory tool — they have no source, and a backfill is a guess.
- The Rooms query is `messages LEFT JOIN memories`, so an unindexed column
  would scan the table on every room render.

Adding the column is non-destructive. The model-side change lives in
`friday/store/schema.py`; the runtime side is the `memory_add` tool now
carrying the message id it ran for.

Revision ID: e7b9a1f3c2d4
Revises: f43e90e01c01
Create Date: 2026-09-08 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7b9a1f3c2d4"
down_revision: str | Sequence[str] | None = "93369d0ea345"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("memories", schema=None) as batch_op:
        batch_op.add_column(sa.Column("source_message_id", sa.String(), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_memories_source_message_id"),
            ["source_message_id"],
            unique=False,
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("memories", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_memories_source_message_id"))
        batch_op.drop_column("source_message_id")
