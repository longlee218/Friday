"""a model call names the task and node it came from

`message_id` suits triage — one call, one message — and nothing downstream: an
extractor runs on every pass of a task's graph against many messages, and a
responder answers a task. Ticket 01 made both of them record, and their rows
landed under no key at all.

`attempts` is deliberately not here. There is no retry at the model layer yet,
so the column would hold 1 on every row — a number that reads like a
measurement and is not one. It comes with ticket 04, which creates values for
it.

Revision ID: 6427dfcb35e2
Revises: 4e804de1021c
Create Date: 2026-09-05 19:45:27.522223

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6427dfcb35e2'
down_revision: Union[str, Sequence[str], None] = '4e804de1021c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('model_calls', schema=None) as batch_op:
        batch_op.add_column(sa.Column('task_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('node', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('latency_ms', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_model_calls_task_id'), ['task_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('model_calls', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_model_calls_task_id'))
        batch_op.drop_column('latency_ms')
        batch_op.drop_column('node')
        batch_op.drop_column('task_id')
