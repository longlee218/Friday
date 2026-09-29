"""The audit-log repository — a `Database` mixin (ticket 17).

Append and read, and nothing else: there is deliberately no update or delete
here. The append-only invariant is DESIGN-v2 §12's, enforced by there being no
method that could break it — a store cannot rewrite history it has no verb for.
The kernel's `friday.kernel.audit` is the only caller.
"""

from __future__ import annotations

from friday.store._common import *


class AuditRepo:
    async def append_audit(
        self, *, event: str, actor: str | None = None, detail: dict | None = None
    ) -> None:
        """Write one line. Never updated, never deleted."""
        async with self._sessions.begin() as session:
            session.add(
                schema.AuditEntry(
                    event=event, actor=actor, detail=detail or {}, at=_now()
                )
            )

    async def audit_entries(
        self, *, event: str | None = None, limit: int | None = None
    ) -> list[AuditEntry]:
        """The log, newest last (the order it was written). Filtered to one
        `event` kind when asked — which is how the MCP-grant check finds the
        last grant recorded for a server, and how a reader asks for approvals."""
        query = select(schema.AuditEntry)
        if event is not None:
            query = query.where(schema.AuditEntry.event == event)
        async with self._sessions() as session:
            rows = await session.scalars(
                query.order_by(schema.AuditEntry.id).limit(limit)
            )
            return [_audit(row) for row in rows]
