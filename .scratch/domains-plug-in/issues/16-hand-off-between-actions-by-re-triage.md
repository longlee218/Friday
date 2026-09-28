Type: grilling
Status: resolved
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

## Answer

Decided 2026-09-28 (grilling).

```
agent → retriage(reason, found)      core terminal tool, agents only → outcome Retriage (stored)
pass  → retriage step (one DBOS step, before deliver)
          tried = every action already run on this task
          len(tried) > len(actions) - 1 → HandOver retriages_exhausted
          triage(whole conversation + retriage note, labels = actions − tried, no skip)
            confident → deliver: tasks.type = new action, pass_no+1 → pending
            low       → deliver: needs_human, retriage note on the card
```

1. **The signal is a new core terminal tool `retriage(reason, found)`**,
   given to every agent beside `ask_reporter` / `hand_over` / `replan`; the
   outcome is a `Retriage`. `replan` = same action, new direction (inside the
   contract); `retriage` = leave the contract. **Agents only** — the Planner
   sees only the intake context, the same text triage read, so it has no new
   evidence to overturn it. The four step types and GatePlan are unchanged.
2. **What triage sees the second time**: the whole conversation (as Intake
   re-runs it) plus a **retriage note** (`from`, `reason`, `found`, refs
   kept). No partial `Diagnosis`/`Explanation` exists — `found` is what
   carries over.
3. **Tried actions are removed from the label `Literal` but stay in the
   prompt** under an "Already tried" section with the agent's reason, so the
   schema can't pick them again and the model sees why they were wrong.
   Every action tried on the task is excluded, not just the last — no X → Y →
   X by construction. `skip` is removed on re-triage: the task was
   acknowledged and the agent read real evidence; "not Friday's work" is
   `hand_over`.
4. **The bound is `len(actions) - 1`, computed at boot** — no `config.yaml`
   knob (ticket 07 holds); adding an action raises it. It is the natural
   limit the exclusion already imposes, so it only names it. Over it →
   `HandOver` `retriages_exhausted`, operator only. Re-triage does **not**
   count toward `max_replans` (that belongs to one action's contract).
5. **Low confidence** (below the shared `confidence_threshold`) → the
   existing `needs_human` path; the card shows the retriage note so the
   operator can assign an action by hand.
6. **Same task, new pass**: `tasks.type` changes, `pass_no + 1`, back to
   `pending`; the next pass runs Intake → Planner → … from the start. The
   triage call is its own DBOS step before `deliver` so a crash never
   triages twice. `elapsed` and replans reset (new contract); asks keep
   counting (`MAX_ASKS_PER_TASK` bounds the reporter's effort per task); the
   re-triage count lives on the task.
7. **Old step results stay** in the table (board, `retriages_exhausted`);
   the new action's agents have other `step_key`s so nothing is reused by
   mistake. The new action's Planner sees the retriage note.
8. **The reporter hears nothing new**: acknowledge stays once per task
   (ticket 15); the final reply waits for approval as usual.
9. **Board**: a timeline line "re-triaged `X` → `Y` — `<reason>`", `found`
   on expand, read from the stored `Retriage`; the old action's plan shown
   collapsed. No new table.
10. **Eval**: `evals/triage.jsonl` rows may carry `retriage_note` + `tried`;
    the runner builds the "Already tried" section and the narrowed `Literal`
    as live. Synthetic rows now (answer_question ↔ trace_problem both ways,
    one whose gold is low confidence); real rows after launch when the
    operator confirms, and the confirmed final classification feeds the
    existing DB-examples path. A prompt change runs `run_triage_eval`.

**Amends 13**: a fourth core terminal tool (`retriage`). New `HandOver`
reason: `retriages_exhausted` (beside `planner_failed`, `step_failed`,
`replans_exhausted`, `out_of_time`, `asks_exhausted`). **Amends 02**: the
assembled triage prompt gains the "Already tried" section and a per-call
label set. Terms for `CONTEXT.md` § Vocabulary at build time: *re-triage*,
*retriage note*.
