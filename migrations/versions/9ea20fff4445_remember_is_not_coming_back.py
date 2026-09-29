"""remember is not coming back

`observations` staged what a step learned, for a promotion pass to turn into
`notes` once an approved outcome corroborated it — and nothing has written an
observation since `remember` was removed from `friday/kernel/tools/`, months before
this migration. The tier had no producer, so the rebuild of every channel's
context file that rode its cadence never fired either, silently, the whole
time.

The operator's replacement (ticket 09's D9) is a memory an agent writes and
reads back directly, scoped to one room, through a tool call — not a staging
tier a human has to approve into existence. This drops what the old design
needed and the new one does not: an approval-gated promotion of a guess into
a belief.

Destructive: any row either table held is gone, and the down migration
recreates the shape, not the data. Both tables have had no writer for as long
as the fact above has been true, so the operator's decision to remove the
tier is a decision that nothing here has data behind it either way.

Revision ID: 9ea20fff4445
Revises: e2218d1e422c
Create Date: 2026-09-06 01:42:54.991889

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9ea20fff4445"
down_revision: str | Sequence[str] | None = "e2218d1e422c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_table("notes")
    with op.batch_alter_table("observations", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_observations_task_id"))

    op.drop_table("observations")


def downgrade() -> None:
    """Downgrade schema."""
    op.create_table(
        "observations",
        sa.Column("id", sa.INTEGER(), nullable=False),
        sa.Column("task_id", sa.INTEGER(), nullable=False),
        sa.Column("category", sa.VARCHAR(), nullable=False),
        sa.Column("text", sa.VARCHAR(), nullable=False),
        sa.Column("created_at", sa.VARCHAR(), nullable=False),
        sa.Column("promoted_at", sa.VARCHAR(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("observations", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_observations_task_id"), ["task_id"], unique=False
        )

    op.create_table(
        "notes",
        sa.Column("category", sa.VARCHAR(), nullable=False),
        sa.Column("text", sa.VARCHAR(), nullable=False),
        sa.Column("support", sa.INTEGER(), nullable=False),
        sa.Column("created_at", sa.VARCHAR(), nullable=False),
        sa.PrimaryKeyConstraint("category", "text"),
    )
