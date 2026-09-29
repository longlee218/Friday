"""rename devops identifiers to the devops namespace

Revision ID: 7f31db1381ef
Revises: 8fe3db098f79
Create Date: 2026-09-23 17:13:18.515654

Ticket 14 lifted the `api_issue` work out to `plugins/devops/`. The plugin's
identifiers are namespaced under `devops` now — the task type `api_issue` is
`devops.api_issue`, the pack kinds `service`/`route`/`environment`/`project`/
`dependency` are `devops.*`, and the model agent `diagnose` is `devops.diagnose`
— while the code that reads and writes them uses the new names. This migration
rewrites the rows a running install already holds so the stored history keeps
matching the registries; without it a right-marked classification of
`"api_issue"` stops matching `registry.decisions()`, an in-flight task's
checkpoint orphans, and a stored `finding` written by `diagnose` reads as an
agent that no longer exists.

**Data only — no schema changes.** Every column touched is a validated-string
identifier column, each grep-checked against the schema before this was written:

  api_issue -> devops.api_issue
    tasks.type, messages.decision_type, node_runs.dag_name, dag_state.dag_name

  <kind> -> devops.<kind> (service/route/environment/project/dependency),
  runbook -> skill
    memories.kind, memory_candidates.kind

  diagnose -> devops.diagnose
    model_calls.agent, tool_calls.agent, memories.agent, memory_candidates.agent

Deliberately untouched, and why: `verdicts.mark` is the closed right/wrong set,
not a type name; `outbox.kind` is the outbound `Kind` enum (announce/reply/…),
not a memory kind; node-name columns (`node_runs.node`, `dag_state.paused_at_node`,
`model_calls.node`, `tool_calls.node`) are graph-internal and not namespaced;
the core kinds `finding`/`person` and the shared agents `extractor`/`triage`/
`responder` stay unprefixed.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7f31db1381ef"
down_revision: str | Sequence[str] | None = "8fe3db098f79"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: (table, column) -> the value renames to apply to it. One place, read by both
#: directions, so the downgrade cannot drift from the upgrade.
_TASK_TYPE = {"api_issue": "devops.api_issue"}
_KINDS = {
    "service": "devops.service",
    "route": "devops.route",
    "environment": "devops.environment",
    "project": "devops.project",
    "dependency": "devops.dependency",
    "runbook": "skill",
}
_AGENT = {"diagnose": "devops.diagnose"}

_RENAMES: dict[tuple[str, str], dict[str, str]] = {
    ("tasks", "type"): _TASK_TYPE,
    ("messages", "decision_type"): _TASK_TYPE,
    ("node_runs", "dag_name"): _TASK_TYPE,
    ("dag_state", "dag_name"): _TASK_TYPE,
    ("memories", "kind"): _KINDS,
    ("memory_candidates", "kind"): _KINDS,
    ("model_calls", "agent"): _AGENT,
    ("tool_calls", "agent"): _AGENT,
    ("memories", "agent"): _AGENT,
    ("memory_candidates", "agent"): _AGENT,
}


def _apply(mapping_selector) -> None:
    """Rewrite each (table, column) by the mapping, in the direction the
    selector chooses — `dict.items()` for upgrade, reversed for downgrade. One
    UPDATE per (column, old-value), scoped by an equality on the old value, so a
    row already carrying the new name (or an unrelated value) is left alone and
    the migration is safe to re-run."""
    for (table, column), renames in _RENAMES.items():
        handle = sa.table(table, sa.column(column, sa.String))
        for old, new in mapping_selector(renames):
            op.execute(
                handle.update().where(handle.c[column] == old).values({column: new})
            )


def upgrade() -> None:
    _apply(lambda renames: renames.items())


def downgrade() -> None:
    _apply(lambda renames: ((new, old) for old, new in renames.items()))
