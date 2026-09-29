"""merge reply_to and dispatch chain heads

Ticket 26's reply_to migration (45fc7ffac350) and ticket 11's dispatch
chain ended at different revisions, leaving the migration tree with two
heads. Ticket 31 adds a child on the reply_to branch and needs a single
head for  to succeed.

Resolves by declaring both heads as parents of this no-op revision.

"""

from collections.abc import Sequence

revision: str = "71a2c91fa3b0"
down_revision: Sequence[str] = ("45fc7ffac350", "4ff2c8660d9e")
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
