Type: grilling
Status: claimed
Blocked by:

# Hand-off between actions by re-triage

## Question

A run finds it is doing the wrong action — `backend.answer_question` ("does
the API validate email?") whose agent sees the request actually failing with
a 400, which is `backend.trace_problem`'s work; or the reverse. Decided in the
fog review (2026-09-28): **Friday re-triages on its own**, and the new triage
gets **added context** so it lands right the second time (not an operator
hand-off, not the Planner switching action by itself).

Decide:

- **The signal**: a new core terminal tool (e.g. `retriage(reason, found)`)
  beside `ask_reporter` / `hand_over` / `replan` (ticket 13), or an ending of
  `replan`; who may raise it — the agent, the Planner, or both.
- **The added context**: what triage sees the second time — the original
  message, the agent's `reason` + `found`, the action already tried (excluded
  or not), the Diagnosis/Explanation so far.
- **The bound**: how many re-triages per task (a core constant?), whether it
  counts toward `max_replans`, and what stops ping-pong between two actions.
- **Low confidence the second time** → the existing `needs_human` path?
- **What happens to the task**: same task with its action changed (a new
  pass, ticket 14's `task-<id>/pass-<n>`), or a new task; what of the stored
  step results and the acknowledgement already sent; does the reporter hear
  anything.
- **Visibility and eval**: how the board shows "re-triaged from X to Y", and
  whether re-triage cases join `evals/triage.jsonl`.
