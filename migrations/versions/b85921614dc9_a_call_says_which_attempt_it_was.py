"""a call says which attempt it was

An ordinal, not a total. One row is one call to the provider and the provider
bills per call, so a run that was rate-limited once leaves two rows — each
with its own prompt and its own cost — rather than one row claiming to be two.
That is what makes the record and the invoice agree, which is the only reason
the column exists.

Nullable, like `dag_state.trail` before it and for the same reason: SQLite
cannot add a NOT NULL column to a table that already has rows, and this one
has them wherever a model has ever been called. A row written before the
column existed says nothing about attempts, which is not the same as saying it
was the first.

Revision ID: b85921614dc9
Revises: 0de9224870e8
Create Date: 2026-09-05 23:47:05.520733

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b85921614dc9"
down_revision: str | Sequence[str] | None = "0de9224870e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("model_calls", schema=None) as batch_op:
        batch_op.add_column(sa.Column("attempt", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("model_calls", schema=None) as batch_op:
        batch_op.drop_column("attempt")
