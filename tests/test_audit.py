"""The append-only audit log (DESIGN-v2 §12, ticket 17).

One record of the security-relevant things that happen — who approved which
bytes, what was loaded and at what tier, an MCP server's tool grant, a refused
decision — that outlives the log file and the process. Append-only: the kernel
appends, and there is deliberately no verb to update or delete a row.
"""

from __future__ import annotations

from friday.kernel.audit import AuditLog
from friday.kernel.domain.conversation import ConversationId
from friday.kernel.outbox import Kind, record_decision

OPERATOR = 42


async def _reply(db) -> int:
    row = await db.queue_outbound(
        task_id=None,
        conversation=ConversationId("discord", "999"),
        kind=Kind.REPLY,
        sender="discord_user",
        text="the api is back up",
    )
    return row.id


async def test_it_only_appends(db):
    """Enforced by shape: the store has no update or delete for the table, and
    the kernel's `AuditLog` exposes only appends."""
    audit = AuditLog(db)
    assert not hasattr(db, "update_audit")
    assert not hasattr(db, "delete_audit")

    await audit.plugin_loaded(plugin_id="core-memory", tier="contribution")
    await audit.plugin_loaded(plugin_id="backend", tier="contribution")

    entries = await db.audit_entries(event="plugin_load")
    assert [e.detail["plugin"] for e in entries] == ["core-memory", "backend"]
    assert all(e.detail["tier"] == "contribution" for e in entries)


async def test_an_approval_records_who_and_which_bytes(db):
    audit = AuditLog(db)
    reply_id = await _reply(db)

    ok = await record_decision(
        db,
        outbound_id=reply_id,
        approved=True,
        by="longle_",
        by_id=OPERATOR,
        operator_id=OPERATOR,
        audit=audit,
    )

    assert ok
    (entry,) = await db.audit_entries(event="approval")
    assert entry.actor == "longle_"
    assert entry.detail["outbound_id"] == reply_id
    assert entry.detail["approved"] is True
    # "which bytes": the frozen payload hash, the same the outbox checks at
    # dispatch — the record names the exact message that was released.
    row = await db.outbound_row(reply_id)
    assert entry.detail["payload_hash"] == row.approved_payload_hash


async def test_a_decision_by_the_wrong_person_is_recorded_as_refused(db):
    audit = AuditLog(db)
    reply_id = await _reply(db)

    ok = await record_decision(
        db,
        outbound_id=reply_id,
        approved=True,
        by="stranger",
        by_id=999,
        operator_id=OPERATOR,
        audit=audit,
    )

    assert ok is False
    assert await db.audit_entries(event="approval") == []
    (refused,) = await db.audit_entries(event="refused_decision")
    assert refused.actor == "stranger"
    assert refused.detail["by_id"] == 999
    assert "operator" in refused.detail["reason"]


async def test_an_mcp_grant_is_recorded_and_only_when_it_changes(db):
    audit = AuditLog(db)

    await audit.mcp_grant(server="loki", tools=["query_range", "labels"])
    await audit.mcp_grant(server="loki", tools=["labels", "query_range"])  # same set
    await audit.mcp_grant(server="loki", tools=["query_range"])  # changed

    grants = await db.audit_entries(event="mcp_grant")
    assert [g.detail["tools"] for g in grants] == [
        ["labels", "query_range"],  # sorted, recorded once
        ["query_range"],  # the change
    ]


async def test_grants_for_different_servers_do_not_collide(db):
    audit = AuditLog(db)

    await audit.mcp_grant(server="loki", tools=["a"])
    await audit.mcp_grant(server="grafana", tools=["b"])
    await audit.mcp_grant(server="loki", tools=["a"])  # unchanged for loki

    grants = await db.audit_entries(event="mcp_grant")
    assert [(g.detail["server"], g.detail["tools"]) for g in grants] == [
        ("loki", ["a"]),
        ("grafana", ["b"]),
    ]
