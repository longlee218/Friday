"""the build respects a budget

Ticket 08 of `.scratch/what-the-room-already-knows/` (D6). Node 0's own
budget-based truncation of a task's transcript needs somewhere to record
that truncating did not help — a single message larger than the budget,
which dropping older messages can never fix — so a third and further pass
stops trying and the condition is visible to the operator instead of a
repeating, silent check.

A new table, so nothing here is destructive — there are no rows to lose.

Revision ID: 00696b6871f1
Revises: 41e3adc9a278
Create Date: 2026-09-10 09:49:10.590692

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "00696b6871f1"
down_revision: str | Sequence[str] | None = "41e3adc9a278"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "compaction_state",
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("ineffective_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("task_id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("compaction_state")
