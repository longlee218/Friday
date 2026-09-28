"""memory carries a kind and a lifecycle

Ticket 10 of `.scratch/what-the-room-already-knows/`. A memory says what kind
of thing it is (`kind`, one of `fact`, `constraint`, `finding`, `decision`,
`voice` — D14) and whether it is still current (`status` and `superseded_by`
— D16): correcting a memory's wording (`memory_update`) leaves it `active` in
place, replacing what it claims (`memory_supersede`) marks it `superseded`
and points at the row that replaced it.

The real deployment's `memories` table has zero rows, so this backfills
nothing there. The server defaults are for any other database that already
holds rows: every existing row takes the kind that matches its only possible
writer today, the responder (`voice`), and `active` status — the same
"existing rows take the reader they have today" rule ticket 10 states.

Revision ID: 385fddf60289
Revises: b4ba2cf5d60d
Create Date: 2026-09-09 14:22:41.413947

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '385fddf60289'
down_revision: Union[str, Sequence[str], None] = 'b4ba2cf5d60d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'memories',
        sa.Column('kind', sa.String(), nullable=False, server_default='voice'),
    )
    op.add_column(
        'memories',
        sa.Column('status', sa.String(), nullable=False, server_default='active'),
    )
    op.add_column(
        'memories', sa.Column('superseded_by', sa.String(), nullable=True)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('memories', 'superseded_by')
    op.drop_column('memories', 'status')
    op.drop_column('memories', 'kind')
