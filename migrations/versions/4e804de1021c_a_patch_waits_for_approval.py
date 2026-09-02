"""a patch waits for approval

Revision ID: 4e804de1021c
Revises: a17c3e5b28d0
Create Date: 2026-09-02

Applying a code change is the one dangerous thing a node can do, and it went
through no gate but a word list matched against a model's own prose. This
column holds the SDK's own run state while a `needs_approval` tool call is
waiting on the operator's yes or no — nullable, and empty for every ordinary
task, which is every task that never reaches that call.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '4e804de1021c'
down_revision: Union[str, Sequence[str], None] = 'a17c3e5b28d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'dag_state',
        sa.Column('interruption', sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('dag_state', 'interruption')
