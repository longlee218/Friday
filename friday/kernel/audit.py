"""The append-only audit log (DESIGN-v2 §12).

One place the kernel writes down the security-relevant things that happen, so
that after the fact there is a record of them that is not the log file (which
rotates) or a decision held only in a process that has since restarted:

- **who approved which bytes** — every operator decision on a reply;
- **refused requests** — a decision refused because the decider was not the
  operator (`refused_decision`). This is the only request the kernel refuses
  today; the narrowed-handle refusals §5.1 describes have no path yet (a
  plugin's `deps`/`needs` that cannot be satisfied fails the boot with a
  `ConfigError`, before this log opens), so that entry is added with that
  path, on its trigger (§16), not shipped inert ahead of it;
- **plugin loads with tiers** — what was loaded into the process and at what
  trust level (§3.1);
- **MCP-grant changes** — the code-declared tool allow-list each server was
  built with, an entry only when it changes from the last one recorded.

**Append-only is the whole point** and it is enforced by shape: this class
appends, and the store repository (`friday.store.repositories.audit`) has no
update or delete for the table. The database file is not itself tamper-proof; a
hash chain over these rows is deferred until there is a second principal or an
external auditor to want it (§16). Writes are best-effort — auditing a thing
must never be the reason the thing fails — so a store error here is logged and
swallowed rather than raised into the caller's path.
"""

from __future__ import annotations

import logging
from typing import Any

__all__ = ["AuditLog"]

log = logging.getLogger(__name__)


class AuditLog:
    """The kernel's one door to the audit log. Holds a `Store` it only appends
    through."""

    def __init__(self, db: Any) -> None:
        self._db = db

    async def _append(self, event: str, *, actor: str | None, detail: dict) -> None:
        try:
            await self._db.append_audit(event=event, actor=actor, detail=detail)
        except Exception as exc:  # noqa: BLE001 - auditing must not fail the act
            log.warning("could not write audit entry %r: %s", event, exc)

    async def approval(
        self, *, outbound_id: int, by: str, approved: bool, payload_hash: str | None
    ) -> None:
        """An operator's decision on a reply. `payload_hash` is *which bytes* —
        the frozen digest the outbox checks at dispatch — so the record names
        the exact message that was released, not just that something was."""
        await self._append(
            "approval",
            actor=by,
            detail={
                "outbound_id": outbound_id,
                "approved": approved,
                "payload_hash": payload_hash,
            },
        )

    async def refused_decision(
        self, *, outbound_id: int, by: str, by_id: int, reason: str
    ) -> None:
        """A decision the kernel would not honour — today, a press by someone
        who is not the operator on a reply that speaks in the operator's name."""
        await self._append(
            "refused_decision",
            actor=by,
            detail={"outbound_id": outbound_id, "by_id": by_id, "reason": reason},
        )

    async def plugin_loaded(self, *, plugin_id: str, tier: str) -> None:
        """A plugin loaded into the process, and at what trust tier (§3.1)."""
        await self._append(
            "plugin_load", actor=None, detail={"plugin": plugin_id, "tier": tier}
        )

    async def mcp_grant(self, *, server: str, tools: list[str]) -> None:
        """The tool allow-list an MCP server was built with. Recorded only when
        it differs from the last grant recorded for this server, so the log
        carries changes rather than one identical line every boot (§12)."""
        tools = sorted(tools)
        prior = await self._db.audit_entries(event="mcp_grant")
        for entry in reversed(prior):
            if entry.detail.get("server") == server:
                if entry.detail.get("tools") == tools:
                    return
                break
        await self._append(
            "mcp_grant", actor=None, detail={"server": server, "tools": tools}
        )
