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

## Review fixes
The review of `95d800a` found one must-fix. A press on a card from before
this change (`friday:approve:<task>`) reached `handle`, which returned
without doing anything, and `_Buttons._answered` then edited the card to
"_answered by X_" anyway. The operator saw a decision recorded when nothing
was approved, and a rejection handed nothing over. That is a dropped
decision that looks like a recorded one.

Fixed. `handle` now returns whether it reported a decision, and logs a
warning naming the custom_id whenever it ignores a press. `_answered` writes
"answered by" only when a decision was recorded. Otherwise the card says
"nothing was recorded: this card does not name a reply. If it still needs
sending, answer in the thread yourself." Tests:
`test_a_recorded_decision_says_who_answered` and
`test_a_card_from_before_the_move_says_nothing_was_recorded` (the edit and
the log line).

**Recovery path for replies left waiting at upgrade.** The migration gives
old cards no `approves` and queues no new card, so a reply that was waiting
for approval at upgrade time has no working card. The recovery is the
operator answering in the thread by hand. Their own message moves the task
to `handled_by_operator`, and `Pool` withdraws every queued row for it
(`cancel_outbound_for`), including the stale reply. No new card is
re-queued. That would mean inserting outbox rows from a migration, or
adding a boot-time sweep, for a window that only exists once, at this
upgrade.

In practice the stale card may never reach `_answered`. The upgrade is a
restart, and after a restart only the persistent view registered by `start`
is dispatched. That view's ids end in `None` (see "Noticed" above), so a
press on an old card most likely gets Discord's own "interaction failed"
rather than any edit. The fix still matters for the process that sent a
card and is still running.

Suite after the fixes: `1 failed, 1231 passed, 1 skipped`. The one failure
is still the baseline `test_doc_paths_resolve_to_existing_files`. Guards
deleted once and watched go red, then restored: the "answered by" note
gated on `recorded`, the warning on a press that names no reply, and
`handle` returning `True` after a reported decision.

The review also flagged the CLAUDE.md item below as not done. It is still
owed. This lane may not edit CLAUDE.md while the operator rewrites it
elsewhere.

## Docs owed
- CLAUDE.md, the "Nothing is sent by the caller that decided to send it"
  constraint: add that approval lives on the outbox row
  (`outbox.approved_at`), not on the task, and that the approval card names
  the row it approves (`approves`). Nothing in CLAUDE.md now says the
  opposite, but this is a load-bearing decision it does not record.
