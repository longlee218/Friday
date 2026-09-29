"""dag state knows its inputs

Revision ID: a17c3e5b28d0
Revises: 8f31d4a90c25
Create Date: 2026-09-01

A graph's results are only meaningful for the parameters it ran against.
Without this, the sequence that matters most goes wrong: the graph finds
nothing to trace, asks the reporter for a correlationId, gets one — and then
resumes, sees every node already recorded, and parks without reading a single
log line. Worse than the planner it replaced, which had no memory and so
reconsidered every time.

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a17c3e5b28d0"
down_revision: str | Sequence[str] | None = "8f31d4a90c25"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "dag_state",
        sa.Column("params_fingerprint", sa.String(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("dag_state", "params_fingerprint")
