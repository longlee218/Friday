"""a candidate memory waits for the mark

Ticket 12 of `.scratch/what-the-room-already-knows/` (D19, D20). A memory an
agent proposes is never read by a prompt or a tool until the operator marks
it, so it lives in its own table rather than a status on `memories` — every
reader that serves a model would otherwise need to remember to filter a
pending row out, which is the guarantee D20 asks to hold structurally.

`source_message_id` is what resolves a candidate: the same message the
operator already reacts to in order to confirm or reject the classification
that opened the task it came from (D19 — "the gesture that already confirms
a classification"). `memory_id` is set only once accepted, and only if the
write actually landed.

A new table, so nothing here is destructive — there are no rows to lose.

Revision ID: a423973bc96d
Revises: 00696b6871f1
Create Date: 2026-09-10 23:15:05.467389

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a423973bc96d"
down_revision: str | Sequence[str] | None = "00696b6871f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "memory_candidates",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("channel_id", sa.String(), nullable=False),
        sa.Column("agent", sa.String(), nullable=False),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("source_message_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("proposed_at", sa.String(), nullable=False),
        sa.Column("resolved_at", sa.String(), nullable=True),
        sa.Column("resolved_by", sa.String(), nullable=True),
        sa.Column("memory_id", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("memory_candidates", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_memory_candidates_channel_id"), ["channel_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_memory_candidates_proposed_at"),
            ["proposed_at"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_memory_candidates_source_message_id"),
            ["source_message_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_memory_candidates_status"), ["status"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_memory_candidates_task_id"), ["task_id"], unique=False
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("memory_candidates", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_memory_candidates_task_id"))
        batch_op.drop_index(batch_op.f("ix_memory_candidates_status"))
        batch_op.drop_index(batch_op.f("ix_memory_candidates_source_message_id"))
        batch_op.drop_index(batch_op.f("ix_memory_candidates_proposed_at"))
        batch_op.drop_index(batch_op.f("ix_memory_candidates_channel_id"))

    op.drop_table("memory_candidates")
