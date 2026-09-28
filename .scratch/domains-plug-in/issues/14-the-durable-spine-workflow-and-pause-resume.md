Type: grilling
Status: resolved
Blocked by: 04, 13

# The durable spine workflow and pause/resume

## Question

Today only a task's graph (`_run_graph`) and each outbound send are DBOS
workflows; Intake/node 0 runs outside, in the pool's polling loop. Decide the
spine's durable boundary: one workflow per task from Intake to Draft? What the
pool still does (claim, concurrency, help-wanted). How `Ask` pauses from any
step and how a reporter's reply resumes it — Intake re-run, `placement_identity`
diff, continue vs re-plan — and whether this subsumes `build-the-loop` ticket 4.

> Note from "GatePlan" (11): every plan version — refused ones with their gate
> errors included — must be stored where the board can read it.

> Note from "The Planner" (12): a gate refusal is rewritten in the same
> Planner conversation, so its `message_history` must persist durably between
> the Planner step and the GatePlan step. Planner time counts toward the
> task's elapsed time.

> Note from "The WorkflowRunner and adaptive replan" (13): every result,
> `Ask` included, is stored at `(task_id, step_key)` and reused when the key
> matches — so on a reply, the stored `Ask` must be dropped (or keyed apart)
> for its step to re-run and see the reply. A reply-driven replan counts
> toward `max_replans`. Code-authored `HandOver` reasons: `step_failed`,
> `replans_exhausted`, `out_of_time`.

> Note from "`trace_problem`'s graph becomes the first plan" (15): the
> acknowledgement is a spine step between Intake and the Planner — the
> action's `acknowledge` hook, queued once per task without approval; a
> reply's Intake re-run never sends a second one.

## Answer

Decided 2026-09-28 (grilling).

```
Pool          claim pending · concurrency · start/recover the pass · stand_down · raise_hands
Pass          one DBOS workflow  task-<id>/pass-<n>   (n = tasks.pass_no, +1 on every move into pending)
  intake      always fresh (DB only)
  acknowledge once per task (ticket 15) — a later pass never sends a second one
  plan        one step: Planner → gate → ≤2 rewrites → frozen | planner_failed
              runs when: no plan yet · placement changed · agent Replan · operator hand-back
              every version → table `plans` (frozen/refused + gate errors)
  run         steps; results at (task_id, step_key), step_key includes placement_identity
  deliver     queue outbox rows + move task state, one DBOS step
Pause         Ask ends the pass → waiting_for_details; no workflow waits on a person
Reply         → pending → pass n+1 → intake → diff placement_identity
                unchanged → the Ask step continues its message_history + Evidence, reply appended
                changed   → replan (counts toward max_replans); old results never match
Mid-pass msg  outcome Ask + a reporter message newer than this pass's intake → don't send, pass n+1
Reuse rule    skip a step whose stored result is final; Ask = continuation point; HandOver never reused
total_time    sum of pass run-times (waiting excluded)
Hand-back     operator needs_human → pending: reset elapsed, replans, asks; fresh plan (not counted)
```

1. **A pass is one short DBOS workflow**, not one long-lived workflow per
   task. `Ask` ends the pass; nothing waits in `recv`. `DBOS.recv/send`, the
   24h `WAIT_TIMEOUT_SECONDS`, the `pending` event and the pool's `_poll`
   go. DBOS only resumes a pass after a crash; reuse *across* passes is the
   `(task_id, step_key)` table's job (ticket 13).
2. **Routing is the pass's last step, `deliver`**: outbox rows + task state
   in one DBOS step, so the spine is durable Intake → Outbox and "never
   route twice" is DBOS's, not hand-written idempotency. The pool keeps
   claim, concurrency, starting/recovering a pass, `_stand_down`,
   `_raise_hands`. Node 0 no longer runs in the pool.
3. **Pass id = `task-<id>/pass-<n>`**, `n` = a new `tasks.pass_no` column
   (Alembic), incremented in the same transaction as every move into
   `pending`. Restart mid-pass → same id → same workflow; a reply or an
   operator hand-back → `n+1`.
4. **A stored `Ask` is a continuation point, not a final result.** It carries
   the agent's `message_history` + `Evidence` (complete — the agent stopped
   cleanly). On the next pass with unchanged placement, the step re-runs
   *from* that history with the reporter's reply appended: reads not
   repeated, `Lnn` ids stable. Not a mid-loop checkpoint (ticket 13 still
   holds for crashes).
5. **`step_key` includes `placement_identity`.** A reply that changes it →
   replan (counts toward `max_replans`, ticket 13), and no old result — the
   continuation `Ask` included — can match. Old results stay in the table
   for the Planner, the board and `replans_exhausted`. `ops` (identity `()`)
   is never affected.
6. **Planner + GatePlan are one DBOS step `plan`**: Planner, gate, ≤2
   rewrites in one in-memory conversation — `message_history` is never
   persisted. Each version (frozen or refused, with gate errors) is written
   to a new `plans` table (task_id, version, replaces, body, hash,
   gate_errors), idempotent on `(task_id, version)`; the board reads it. A
   crash re-runs the whole planning (≤3 strong-model calls).
7. **`total_time` is machine work time**: the sum of every pass's run time
   (Planner included), waiting for the reporter excluded. A reporter who
   never answers is the `waiting_for_details` state's and `_stand_down`'s
   concern, not the runner's.
8. **A reporter message that lands mid-pass**: checked in `deliver`, only
   when the outcome is `Ask` — a message newer than this pass's Intake →
   the question is not sent, the task goes back to `pending` (pass `n+1`),
   the agent continues with it. `Reply` / `HandOver` route as usual; the
   operator sees the message on the board.
9. **Operator hand-back** (`needs_human` → `pending`) is a fresh run on the
   old ground: `elapsed`, replans and asks reset; a stored `HandOver` is
   never reused (its step re-runs); the Planner writes a new plan (not
   counted), seeing prior plans and results, so a finished `Diagnosis` is
   reused if the plan keeps that step.
10. **Bound on asking**: a core constant `MAX_ASKS_PER_TASK` (3) beside
    `deliver`; over it → `HandOver` `asks_exhausted`. Replaces the deleted
    `workflows.max_asks` (ticket 07) — a reply with unchanged placement is
    not a replan, so `max_replans` does not bound it.
11. **An `Ask` is sent as the agent wrote it** (redacted like every outbox
    row); no responder rewrite. `_in_the_operators_voice` goes; tone lives
    in the agent's instructions.
12. **Subsumes build-the-loop ticket 4** ("Checkpoint / resume on placement
    identity") — closed as superseded; its acceptance moves to the spine's
    build board.

`HandOver` reasons from code, all countable: `planner_failed` (12),
`step_failed`, `replans_exhausted`, `out_of_time` (13), `asks_exhausted`.

Terms for `CONTEXT.md` § Vocabulary at build time: *pass*, *continuation
point*, *hand-back*.

Build consequences: `_run_graph`, `adapter.answer/pending`, `Pool._run_entry
/_run_workflow/_poll/_route/_ask/_in_the_operators_voice` rewritten or
deleted; Alembic adds `tasks.pass_no`, `plans`, the step-results table; the
`WAITING_FOR_DETAILS → PENDING` and `NEEDS_HUMAN → PENDING` moves bump
`pass_no`.

## Amended 2026-09-28 by ticket 17

Point 7 goes: there is no `total_time`, so pass run-times are not summed; `out_of_time` leaves the hand-over reasons. See [The budget in three groups](17-the-budget-in-three-groups.md).
