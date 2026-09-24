"""rename doc_question to the docs namespace

Revision ID: a8e34662ebae
Revises: 7f31db1381ef
Create Date: 2026-09-24

Ticket 15 lifts the `doc_question` persona out to `plugins/docs/`, so its task
type is namespaced `docs.doc_question` (the same dot convention ticket 14 gave
`devops.api_issue`). This rewrites the rows a running install already holds, so a
right-marked `doc_question` classification keeps matching `registry.decisions()`
and an in-flight `doc_question` task's checkpoint is not orphaned.

**Data only.** `doc_question` is a simple type — its node 0 runs the shared
`extractor` agent (which stays core) and it writes no memory kind — so unlike the
devops rename this touches no agent or kind column, only the four that hold the
task-type name, each grep-checked against `friday/store/schema.py`:

  doc_question -> docs.doc_question
    tasks.type, messages.decision_type, node_runs.dag_name, dag_state.dag_name
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a8e34662ebae'
down_revision: Union[str, Sequence[str], None] = '7f31db1381ef'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: The columns that hold the task-type name, and the one rename applied to each.
#: One place, read by both directions so the downgrade cannot drift.
_COLUMNS = (
    ("tasks", "type"),
    ("messages", "decision_type"),
    ("node_runs", "dag_name"),
    ("dag_state", "dag_name"),
)
_OLD = "doc_question"
_NEW = "docs.doc_question"


def _rename(old: str, new: str) -> None:
    """Rewrite `old` to `new` in every task-type column, scoped by an equality
    on the old value so a row already carrying the new name (or an unrelated
    type) is left alone and the migration is safe to re-run."""
    for table, column in _COLUMNS:
        handle = sa.table(table, sa.column(column, sa.String))
        op.execute(handle.update().where(handle.c[column] == old).values({column: new}))


def upgrade() -> None:
    _rename(_OLD, _NEW)


def downgrade() -> None:
    _rename(_NEW, _OLD)
