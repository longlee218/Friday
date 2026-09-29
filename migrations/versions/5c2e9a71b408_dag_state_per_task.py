"""dag state per task

Revision ID: 5c2e9a71b408
Revises: 31a1b5c8e7d4
Create Date: 2026-09-01

Ticket 32 makes a workflow a graph of nodes. The runner records what each node
produced before starting the next, so a restart resumes at the first
unfinished node rather than paying for the finished ones again.

Its own table rather than a column on `tasks`: this is rewritten every few
seconds while a graph runs, and the task row is what the board and the outbox
read.

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5c2e9a71b408"
down_revision: str | Sequence[str] | None = "31a1b5c8e7d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dag_state",
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("dag_name", sa.String(), nullable=False),
        sa.Column("results", sa.JSON(), nullable=False),
        sa.Column("paused_at_node", sa.String(), nullable=True),
        sa.Column("paused_question", sa.String(), nullable=True),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("task_id"),
    )


def downgrade() -> None:
    op.drop_table("dag_state")
