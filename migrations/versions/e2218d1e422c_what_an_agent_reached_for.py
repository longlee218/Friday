"""what an agent reached for

A prompt says what an agent was asked and says nothing about what it did.
Which of the four skill tools an agent actually reaches for — the catalogue by
name, or the search when the catalogue's wording did not surface it — was a
question nothing could answer, and it becomes an expensive one the day a tool
leaves this process with arguments a model chose.

Its own table rather than more columns on `model_calls`: a tool call has no
prompt and no tokens, and a model call has no arguments and no result.

Revision ID: e2218d1e422c
Revises: b85921614dc9
Create Date: 2026-09-06 01:18:20.506499

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e2218d1e422c"
down_revision: str | Sequence[str] | None = "b85921614dc9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "tool_calls",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("agent", sa.String(), nullable=False),
        sa.Column("tool", sa.String(), nullable=False),
        sa.Column("arguments", sa.String(), nullable=False),
        sa.Column("result", sa.String(), nullable=False),
        sa.Column("failed", sa.Boolean(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("message_id", sa.String(), nullable=True),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("node", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("tool_calls", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_tool_calls_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_tool_calls_message_id"), ["message_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_tool_calls_task_id"), ["task_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_tool_calls_tool"), ["tool"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("tool_calls", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_tool_calls_tool"))
        batch_op.drop_index(batch_op.f("ix_tool_calls_task_id"))
        batch_op.drop_index(batch_op.f("ix_tool_calls_message_id"))
        batch_op.drop_index(batch_op.f("ix_tool_calls_created_at"))

    op.drop_table("tool_calls")
