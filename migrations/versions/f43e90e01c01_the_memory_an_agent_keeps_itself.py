"""the memory an agent keeps itself

Ticket 09's D9: an agent writes its own memory and reads it back, scoped to
one channel, replacing a staging-and-promotion tier that had gone months with
no producer (dropped in the migration just before this one).

`id` is a string rather than the usual autoincrement — generated in the store
as an opaque, sparse token, so a model that invents one fails rather than
resolving to whatever row a sequence happens to have reached.

Soft-deleted (`deleted_at`/`deleted_by`) rather than removed on delete: the
operator can see what a line said and who took it out.

A new table, so nothing here is destructive — there are no rows to lose.

Revision ID: f43e90e01c01
Revises: 9ea20fff4445
Create Date: 2026-09-06 01:52:27.094646

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f43e90e01c01'
down_revision: Union[str, Sequence[str], None] = '9ea20fff4445'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('memories',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('channel_id', sa.String(), nullable=False),
    sa.Column('agent', sa.String(), nullable=False),
    sa.Column('text', sa.String(), nullable=False),
    sa.Column('task_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.String(), nullable=False),
    sa.Column('updated_at', sa.String(), nullable=False),
    sa.Column('deleted_at', sa.String(), nullable=True),
    sa.Column('deleted_by', sa.String(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('memories', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_memories_channel_id'), ['channel_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_memories_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_memories_task_id'), ['task_id'], unique=False)



def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('memories', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_memories_task_id'))
        batch_op.drop_index(batch_op.f('ix_memories_created_at'))
        batch_op.drop_index(batch_op.f('ix_memories_channel_id'))

    op.drop_table('memories')
