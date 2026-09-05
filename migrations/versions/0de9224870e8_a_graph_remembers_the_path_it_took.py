"""a graph remembers the path it took

Declaration order is not execution order, and `Pool._outcome` reads the path
backwards to find which node decided what the graph would send. The path lived
only in memory, so a run resumed after a restart answered that question from
whichever part of it that run happened to walk.

**Nullable, and the first draft of this was not.** SQLite cannot add a NOT
NULL column to a table that already has rows — "Cannot add a NOT NULL column
with default value NULL" — and `dag_state` has them on any deployment that has
ever run a graph. A migration that fails half way leaves the schema ahead of
the version and every subsequent start dies on it, which is a boot loop rather
than a warning. A row written before this column existed has no recorded path,
which is a different thing from an empty one, and both read as "nothing to
resume from".

Revision ID: 0de9224870e8
Revises: 6427dfcb35e2
Create Date: 2026-09-05 19:54:30.514673

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0de9224870e8'
down_revision: Union[str, Sequence[str], None] = '6427dfcb35e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('dag_state', schema=None) as batch_op:
        batch_op.add_column(sa.Column('trail', sa.JSON(), nullable=True))



def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('dag_state', schema=None) as batch_op:
        batch_op.drop_column('trail')

