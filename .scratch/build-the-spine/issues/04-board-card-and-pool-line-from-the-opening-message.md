Status: ready-for-agent
Blocked by: 02

# Board card and pool line from the opening message

Decision: [What replaces `params`](../../domains-plug-in/issues/05-what-replaces-params.md)
(board and pool half; the responder half is in 14).

## Goal

- Board card: title = the opening message's text; triage `reason` read from
  that message's `decision_params` (via `task.opening`). No `tasks.reason`.
- Pool log/notification line: `type #id — <first line of the opening
  message>`, then reason and "they last said" as today.
- Neither reads `tasks.params` any more (the column itself goes in 16).

## Acceptance

- [ ] `kernel/ops/api.py` and `pool` read no `params` for card/line (test).
      Split `ops/api.py` along the part touched (09 §9).
- [ ] Web card shows the opening text + reason; `npm run build` passes.
- [ ] Whole suite green; `code-review` done.
