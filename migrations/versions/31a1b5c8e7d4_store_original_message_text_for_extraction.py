"""store original message text for extraction

Revision ID: 31a1b5c8e7d4
Revises: d96f607d0bb9
Create Date: 2026-09-01

Ticket 31 moves extraction from triage to the workflow. The workflow
needs the reporter's raw text to extract from; storing it on the
message row avoids re-fetching from Discord on every plan.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '31a1b5c8e7d4'
down_revision: Union[str, Sequence[str], None] = '71a2c91fa3b0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite has no ALTER COLUMN DROP DEFAULT, so we add the column without
    # a server default and backfill in one go. The Ticket 31 plan can rely
    # on the column being present and non-empty after this migration.
    op.add_column('messages', sa.Column('original_text', sa.String()))
    op.execute("UPDATE messages SET original_text = text")
    # The column is non-null going forward; existing rows are now filled.
    with op.batch_alter_table('messages') as batch:
        batch.alter_column('original_text', existing_type=sa.String(), nullable=False)


def downgrade() -> None:
    op.drop_column('messages', 'original_text')
