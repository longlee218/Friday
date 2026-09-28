"""params the scrub double-encoded

`b7c1a4e93f02` wrote `json.dumps(cleaned)` into `tasks.params`, a `sa.JSON`
column that encodes again, so every task it scrubbed was left holding a JSON
string instead of an object. The pool reads `params` as a dict and the
service stopped booting on the first such task in `needs_human`.

That revision is fixed for a database that has not run it yet; this one
repairs the rows it already wrote. Data only.

Revision ID: c3e8d2a51b47
Revises: b7c1a4e93f02
Create Date: 2026-09-21

"""
from typing import Sequence, Union

import json

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3e8d2a51b47'
down_revision: Union[str, Sequence[str], None] = 'b7c1a4e93f02'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_tasks = sa.table("tasks", sa.column("id", sa.Integer), sa.column("params", sa.JSON))


def upgrade() -> None:
    connection = op.get_bind()
    for row in connection.execute(sa.select(_tasks.c.id, _tasks.c.params)):
        if not isinstance(row.params, str):
            continue
        try:
            params = json.loads(row.params or "{}")
        except json.JSONDecodeError:
            continue
        # Only what the scrub wrote: a dict it encoded twice. Anything else
        # was not this bug, and guessing at it here would stop the boot.
        if isinstance(params, dict):
            connection.execute(
                _tasks.update().where(_tasks.c.id == row.id).values(params=params)
            )


def downgrade() -> None:
    """Nothing. Encoding the rows twice again would only restore the bug."""
