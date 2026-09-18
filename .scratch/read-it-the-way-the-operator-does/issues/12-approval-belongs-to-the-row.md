# 12: Approval belongs to the row, not the task

**What to build:** `approved_at`/`approved_by` on `Outbound`; the
sendable-rows predicate reads the row; the approval card names the row.

**Blocked by:** nothing. **Decisions:** finding A.
**Status:** done

## Why
`Task.approved_at` is written once and never cleared, and the predicate at
`friday/store/db.py:1657` reads it. Any second `reply` on an approved task is
sendable the moment it is queued. No graph queues two today; `api_issue`
queues two by design, and the second is the one that asserts a cause.

## Verify
- A test that fails today: approve one reply, queue a second, assert the
  second is not sendable. Alembic migration; existing approved tasks carry
  their approval onto their existing rows.

## Done
`outbox` carries `approved_at`/`approved_by` per row and `approves` on an
approval card; `tasks.approved_at`/`approved_by` are gone.
`sendable_outbound` reads the row, not the task (no join). `Pool._propose`
queues the card with `approves=reply.id`. The bot's buttons are
`friday:{approve|reject}:row:<id>`, the card text says `reply <id>`, and
`on_decision` reports `outbound_id`. `run_agent.decided` approves that row
through `db.approve_outbound`. A rejection looks up the row's task
(`db.outbound_row`) and hands it over, as before. A three-part card from
before this change carries a task id, so it is ignored rather than read as a
row id. Migration `c2b3bbe390ee` copies each approved task's approval onto
its existing `reply` rows before dropping the task columns. It uses
SQLAlchemy Core, not a SQL string. Approval cards and other tasks' rows get
nothing. The downgrade copies the latest row approval back onto the task.

Suite: `1 failed, 1229 passed, 1 skipped`. The one failure is the baseline
`test_doc_paths_resolve_to_existing_files` (`friday/board/` in CLAUDE.md).

Guards deleted once and watched go red, then restored: the row predicate
(3 outbox tests), the migration's carry step, `approves=reply.id` in
`_propose`, the bot ignoring legacy three-part ids, buttons carrying the
row, and the card text naming the row.

Not done: the `code-review` subagent pass. This ran as a subagent with no
way to spawn another. `decided`'s rejection branch has no test, because
`run_agent._run` is a closure the suite does not drive.

Noticed, not changed: `DiscordBot.start` registers the persistent view as
`_Buttons(None, ...)`. That gives a custom_id ending in `None`, which matches
no real card, so "old cards keep working across a restart" was not true
before this ticket either.

## Docs owed
- CLAUDE.md, the "Nothing is sent by the caller that decided to send it"
  constraint: add that approval lives on the outbox row
  (`outbox.approved_at`), not on the task, and that the approval card names
  the row it approves (`approves`). Nothing in CLAUDE.md now says the
  opposite, but this is a load-bearing decision it does not record.
