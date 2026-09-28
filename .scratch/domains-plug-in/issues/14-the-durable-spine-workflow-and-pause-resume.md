Type: grilling
Status: claimed
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
