# 12: Approval belongs to the row, not the task

**What to build:** `approved_at`/`approved_by` on `Outbound`; the
sendable-rows predicate reads the row; the approval card names the row.

**Blocked by:** nothing. **Decisions:** finding A.
**Status:** ready-for-agent

## Why
`Task.approved_at` is written once and never cleared, and the predicate at
`friday/store/db.py:1657` reads it. Any second `reply` on an approved task is
sendable the moment it is queued. No graph queues two today; `api_issue`
queues two by design, and the second is the one that asserts a cause.

## Verify
- A test that fails today: approve one reply, queue a second, assert the
  second is not sendable. Alembic migration; existing approved tasks carry
  their approval onto their existing rows.
