Status: ready-for-human
Blocked by: 07, 08, 09, 11, 12, 13

# The spine pass; `trace_problem` moves onto it

Decisions: [The durable spine workflow and pause/resume](../../domains-plug-in/issues/14-the-durable-spine-workflow-and-pause-resume.md)
(as amended by 17), [`trace_problem`'s graph becomes the first plan](../../domains-plug-in/issues/15-trace-problem-becomes-the-first-plan.md),
[What replaces `params`](../../domains-plug-in/issues/05-what-replaces-params.md) (responder).
Carries `build-the-loop` ticket 04's acceptance.

## Goal

- `kernel/spine/workflow.py`: one DBOS workflow per pass
  `task-<id>/pass-<n>`: intake → acknowledge (once per task, the action's
  hook, no approval) → plan (Planner + gate, one DBOS step; every version
  in table `plans`, idempotent on `(task_id, version)`) → run → deliver
  (outbox rows + task state, one DBOS step).
- Alembic: `tasks.pass_no`, `plans`; `WAITING_FOR_DETAILS → PENDING` and
  `NEEDS_HUMAN → PENDING` bump `pass_no` in the same transaction.
- `Ask` ends the pass; reply → pass n+1 → Intake → identity unchanged →
  continue the stored `Ask`; changed → replan (counts toward `max_replans`).
- Mid-pass reporter message with outcome `Ask` → not sent, pass n+1.
- Hand-back resets replans and asks, fresh plan. `MAX_ASKS_PER_TASK` (3) →
  `HandOver asks_exhausted`. `Ask` sent verbatim (redacted);
  `_in_the_operators_voice` goes.
- `kernel/pool.py` shrinks to claim, concurrency, start/recover a pass,
  `_stand_down`, `_raise_hands`; `DBOS.recv/send`, `WAIT_TIMEOUT_SECONDS`,
  `_poll` go.
- `plugins/backend/actions/trace_problem/` (Action, contract, `acknowledge`
  = today's `ack_text`, `planning`), `plugins/backend/agents/diagnose.py`
  (`AgentSpec`, result `Diagnosis`). The report file and `reports_dir` go.
- Responder reads the intake context (+ `Outcome`), same no-invention rule.
- `trace_problem` tasks run only on the spine; its DAG is unreachable (deleted in 16).
- `plugins/backend/agents/diagnose_prompt.py`, found in the 2026-09-30 review
  of the working tree (the file moved here from `graph/` in this ticket):
  - `JOB` still says "You have no tools on this path: you cannot read another
    file, run another query" — the dossier mode's text — while `DIAGNOSE`
    builds with `reads=True` and the `READS` steps say "Start with
    `read_log`". Rewrite `JOB` for the mode the agent actually runs in; the
    two-mode `reads` flag goes with the DAG node in 16 if nothing else needs it.
  - `READS` is a reading procedure (which tool first, when to widen), not a
    thinking style; appended to `THINKING` it renders as ten numbered steps.
    Move it into the reads-mode `JOB` or its own section, and keep
    `thinking_style` to how to weigh what was read.
  - `agents/diagnose.py` defines `_rejected` twice (lines 105 and 125,
    identical); keep one.

## Acceptance

- [x] Crash mid-pass resumes the same pass id; nothing sent twice.
- [x] Unchanged-placement reply resumes; `Lnn` stable; reads not repeated.
- [x] A reply flipping env/service re-plans and re-investigates.
- [x] `asks_exhausted`, hand-back reset, mid-pass message tested.
- [ ] `run_api_issue_eval` on the captured cases reported, no worse than before.
- [ ] One real end-to-end run on the operator's machine, observed on the board.
- [ ] Diagnose's prompt says one thing about tools: `JOB` rewritten for
      reads mode, `READS` out of `thinking_style`, one `_rejected`; the
      rendered `build_instructions(reads=True)` read once by a person.
- [x] `CONTEXT.md`: *spine*, *pass*, *hand-back*; `docs/DESIGN.md` § What
      exists describes the spine for `trace_problem`.
- [x] Whole suite green; `code-review` done.

## Left for the operator (2026-09-30)

Code, tests and docs are in. Three boxes wait on a person:

- **Eval**: `uv run run_eval.py backend.trace_problem` now runs the spine's
  `backend.diagnose` step on `data/cases/` (paid). No "before" number exists
  (the DAG replay was never scored), so the first run is the baseline.
- **End to end**: one real report through `uv run run_agent.py`, watched on
  the board.
- **Diagnose prompt**: `JOB` rewritten for reads mode, `READS` moved into it
  and out of `thinking_style`, one `_rejected` — done; the rendered
  `build_instructions(reads=True)` still has to be read by a person.

