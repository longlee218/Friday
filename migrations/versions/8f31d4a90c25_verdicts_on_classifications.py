"""verdicts on classifications

Revision ID: 8f31d4a90c25
Revises: 5c2e9a71b408
Create Date: 2026-09-01

Ticket 29. The operator marks a classification right or wrong with a reaction;
only the ones marked right are ever shown back to the classifier as examples.

One row per message rather than an append-only log: what matters downstream is
what they currently think, and a history of reactions is a history nobody
reads. Taking the mark back deletes the row, because "unmarked" and "never
marked" mean the same thing — nobody is vouching for this one.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '8f31d4a90c25'
down_revision: Union[str, Sequence[str], None] = '5c2e9a71b408'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'verdicts',
        sa.Column('provider', sa.String(), nullable=False),
        sa.Column('provider_message_id', sa.String(), nullable=False),
        sa.Column('mark', sa.String(), nullable=False),
        sa.Column('marked_by', sa.String(), nullable=False),
        sa.Column('marked_at', sa.String(), nullable=False),
        sa.PrimaryKeyConstraint('provider', 'provider_message_id'),
    )


def downgrade() -> None:
    op.drop_table('verdicts')
