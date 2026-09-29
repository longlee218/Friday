"""store original message text for extraction

Revision ID: 31a1b5c8e7d4
Revises: d96f607d0bb9
Create Date: 2026-09-01

Ticket 31 moves extraction from triage to the workflow. The workflow
needs the reporter's raw text to extract from; storing it on the
message row avoids re-fetching from Discord on every plan.

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "31a1b5c8e7d4"
down_revision: str | Sequence[str] | None = "71a2c91fa3b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Each step checks the state it is about to change, because this migration
    # is known to have half-applied: SQLite DDL ran outside a transaction, the
    # column and its NOT NULL landed, the backfill and the version stamp did
    # not, and `alembic upgrade head` then died on `duplicate column name` at
    # every start. `env.py` now runs migrations transactionally so a partial
    # apply cannot happen again; this one has to be able to finish the job it
    # left half done on a database that already exists.
    #
    # Guards on state, not on a version number — a database is what it is,
    # whatever the stamp says.
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("messages")}

    if "original_text" not in columns:
        # SQLite has no ALTER COLUMN DROP DEFAULT, so add without a server
        # default and backfill before making it non-null.
        op.add_column("messages", sa.Column("original_text", sa.String()))
        op.execute("UPDATE messages SET original_text = text")
        with op.batch_alter_table("messages") as batch:
            batch.alter_column(
                "original_text", existing_type=sa.String(), nullable=False
            )
        return

    # The column is already there. Finish what did not run: rows written
    # before this landed carry an empty string, and the extraction pass reads
    # this column to fill in what triage left blank — an empty one is a task
    # that can never have its correlationId found.
    op.execute(
        "UPDATE messages SET original_text = text "
        "WHERE original_text IS NULL OR original_text = ''"
    )


def downgrade() -> None:
    op.drop_column("messages", "original_text")
