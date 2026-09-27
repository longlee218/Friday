Type: grilling
Status: resolved
Blocked by:

# What replaces `params` for the responder, the board and the pool

## Question

With `Params` deleted, three readers lose "what this task knows": the
**responder** (told what the task knows before it drafts), the **board**
(shows a task's known fields), and the **pool's** log/notification line
(`known or 'nothing extracted'`). Decide what each reads instead — the intake
context, the agent's own result, a short summary the agent writes — and whether
any of it needs a schema per action at all.

## Answer

Resolved 2026-09-28 (grilling).

- **Responder** reads the **intake context** (core seed + the domain
  enricher's output), plus the agent's `Outcome` once there is one. The
  anti-invention rule stays — "never say we have a value the context shows
  as null" — now over the intake context. It runs inside the spine and gets
  the context in-process; nothing is stored for it.
- **Nothing "the task knows" is persisted.** `tasks.params` is dropped by an
  Alembic migration, carried by the rename migration (renaming-and-relabelling
  ticket). Old extracted values are lost; the opening message keeps them
  recoverable. No display snapshot of the intake context — a second copy of
  the truth that goes stale.
- **Board card**: title = the opening message's text; triage's `reason` read
  from that message's `decision_params` (already stored, already reached via
  `task.opening`) — no `tasks.reason` column.
- **Pool line**: `type #id — <first line of the opening message>`, then the
  reason and "they last said" as today.
- **No per-action schema.** The only typed pieces are the domain enricher
  (per domain, core-intake ticket) and each agent's declared `Outcome` (plugin
  API ticket). `_plan` / `_as_params`' params-shape check goes with `Params`.
