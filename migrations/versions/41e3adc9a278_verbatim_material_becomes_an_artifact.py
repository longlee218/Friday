"""verbatim material becomes an artifact

Ticket 07 of `.scratch/what-the-room-already-knows/` (D8). Code, stack
traces, SQL, logs and configuration a message carries are split out and
stored whole as their own row — an `artifacts` table, new — rather than
paraphrased by a summariser. `messages.redacted_text` is the message's own
text with each such span swapped for a reference to the artifact it became;
written once, at record time, only for a message that actually carried code,
so it is nullable and every pre-existing row is left `NULL` — meaning "read
`text` instead", which is exactly what an unaffected row already renders.

Revision ID: 41e3adc9a278
Revises: 385fddf60289
Create Date: 2026-09-09 16:26:04.983155

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "41e3adc9a278"
down_revision: str | Sequence[str] | None = "385fddf60289"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("channel_id", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("source_message_id", sa.String(), nullable=False),
        sa.Column("content", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("artifacts", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_artifacts_channel_id"), ["channel_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_artifacts_source_message_id"),
            ["source_message_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_artifacts_created_at"), ["created_at"], unique=False
        )

    op.add_column("messages", sa.Column("redacted_text", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("messages", "redacted_text")

    with op.batch_alter_table("artifacts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_artifacts_created_at"))
        batch_op.drop_index(batch_op.f("ix_artifacts_source_message_id"))
        batch_op.drop_index(batch_op.f("ix_artifacts_channel_id"))

    op.drop_table("artifacts")
