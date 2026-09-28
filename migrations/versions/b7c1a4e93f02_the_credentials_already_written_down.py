"""the credentials already written down

Board `read-it-the-way-the-operator-does`, ticket 17, finding C. From this
revision on, a reporter's own Bearer token is scrubbed where the artifact is
written and where the extractor reads — but three kinds of row written before
that still hold one.

Data only; no schema changes, so autogenerate has nothing to say about it and
`tests/test_migrations.py` stays green either way.

**What it rewrites**, using the same `friday.kernel.ops.redact.scrub` the live path
now uses, so one pattern covers both and they cannot drift:

- `artifacts.content` — the verbatim span, which every later reader copies.
- `tasks.params.curl` — the parameter, for every task type that has one.
- `outbox.text` — the `help_wanted` rows that quote a request into Discord.

**What it does not do is repair a retyped curl.** Ticket 18 found that
`tasks` row 6 holds 676 characters of a 678-character token, and its own
"Done" section records where the reporter's real bytes still are
(`artifacts.af85b208fd70e`). That repair needs a join from a task to the
right artifact and a judgement about which one, and a judgement does not
belong in a migration. After this revision the stored curl carries
`[REDACTED]` where the credential was, so nobody can paste it and meet the
401 that started this either way.

**The downgrade cannot undo it**, and says so rather than pretending. A
credential that has been removed is not recoverable from the row it was
removed from — that is the whole point of removing it.

Revision ID: b7c1a4e93f02
Revises: f81f63e7d3ce
Create Date: 2026-09-20

"""
from typing import Sequence, Union

import json

from alembic import op
import sqlalchemy as sa

from friday.kernel.ops.redact import scrub


# revision identifiers, used by Alembic.
revision: str = 'b7c1a4e93f02'
down_revision: Union[str, Sequence[str], None] = 'f81f63e7d3ce'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: Named rather than reflected, so this migration keeps working against a
#: schema that grows columns after it — the reason every migration here
#: builds its own lightweight table.
_artifacts = sa.table(
    "artifacts", sa.column("id", sa.String), sa.column("content", sa.Text)
)
_tasks = sa.table("tasks", sa.column("id", sa.Integer), sa.column("params", sa.JSON))
_outbox = sa.table("outbox", sa.column("id", sa.Integer), sa.column("text", sa.Text))


def upgrade() -> None:
    connection = op.get_bind()

    for row in connection.execute(sa.select(_artifacts.c.id, _artifacts.c.content)):
        cleaned = scrub(row.content or "")
        if cleaned != row.content:
            connection.execute(
                _artifacts.update()
                .where(_artifacts.c.id == row.id)
                .values(content=cleaned)
            )

    for row in connection.execute(sa.select(_tasks.c.id, _tasks.c.params)):
        params = row.params
        if isinstance(params, str):
            params = json.loads(params or "{}")
        if not isinstance(params, dict):
            continue
        cleaned = {
            key: scrub(value) if isinstance(value, str) else value
            for key, value in params.items()
        }
        if cleaned != params:
            connection.execute(
                _tasks.update()
                .where(_tasks.c.id == row.id)
                .values(params=cleaned)
            )

    for row in connection.execute(sa.select(_outbox.c.id, _outbox.c.text)):
        cleaned = scrub(row.text or "")
        if cleaned != row.text:
            connection.execute(
                _outbox.update().where(_outbox.c.id == row.id).values(text=cleaned)
            )


def downgrade() -> None:
    """Nothing. A credential this removed is gone from the row it was in, and
    inventing one back would be worse than the gap."""
