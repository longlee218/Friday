# 12: A pending patch outlives the task it belonged to

**What to build:** A patch waiting on the operator is withdrawn when the work
it belongs to ends — the same way a queued reply already is. Approving one
afterwards neither runs the tool nor raises in the operator's face.

**Blocked by:** None (can start immediately)

**Decisions:** D15, and the "the operator's own message ends the work" rule

**Status:** done

## Why

Two guarantees this codebase already makes do not currently cover the
interruption ticket 07 added.

**The operator answering does not withdraw a pending patch.** `_stand_down`
cancels the outbound rows and moves the task to `handled_by_operator` —
CLAUDE.md: "everything queued about it is withdrawn." `dag_state.interruption`
is not touched. A later approval then walks the whole of
`decide_pending_action`: it resumes the SDK run, **the approved tool actually
executes**, and only afterwards does `_route` reach `_move`, where
`handled_by_operator → review` is not a legal transition and `move_task`
raises `IllegalTransition`. So the side effect happens on a task a person
already closed, and the operator gets an exception rather than a refusal.
Latent until the buttons land (ticket 18-20), which is the argument for
fixing it before they do rather than after.

**`decide_pending_action`'s own reasoning for not checking staleness is
false.** Its docstring says: "the moment any later pass runs at all, it
overwrites `interruption` with whatever that pass left, `None` if nothing
did. Reaching this with a *specific* interruption still on the row means
nothing has touched it since." A pass whose node 0 returns an **`Ask`** does
not write the row at all — `_run_dag` records a pause only for a `HandOver`
and returns the `Ask` straight out. So a pass can run, change the task, and
leave the interruption sitting there, which is the exact case the docstring
says cannot happen.

Two more from the same review, smaller but in the same machinery:

- `_continue_from` reuses `stored["params_fingerprint"]` wholesale while
  injecting the task's *current* params at `dag.entry` — results computed
  against old parameters, relabelled with new ones, which is what D7 exists
  to prevent. `db.dag_interruption`'s docstring advertises a check
  ("whether the stored state is still good for the task's current parameters
  is exactly what resuming has to check") that no code performs.
- `harness.resume`'s `(item,) = state.get_interruptions()` sits outside
  `_settle`'s try/except, so a run that paused on two tool calls at once
  raises `ValueError` past every "a failure becomes work for a person"
  guarantee in that module, leaving the task stuck with its row intact.

None of the four is reachable in today's wiring — nothing calls
`decide_pending_action` yet, and only `waiting_for_details` returns to
`pending`. That is what makes this the right time: the cost is a few lines
now, versus a debugging session once buttons make all of it live.

## Acceptance criteria

- [x] `_stand_down` clears a pending interruption along with the queued rows,
      so approving after the operator answered does nothing rather than
      applying a patch and raising
- [x] `decide_pending_action` refuses on a task that is no longer in a state
      where the decision means anything, before it resumes the run
- [x] Resuming re-checks that the stored state still matches the task's
      current parameters, or the docstring that promises that check is
      corrected to say what actually happens
- [x] A run paused on more than one approval fails the way every other
      harness failure does — `last_error` and a hand-over — not an unhandled
      `ValueError`
- [x] Each guard added is deleted once and watched go red
- [x] Whatever is decided about staleness is written down where the next
      reader looks: the docstring, or CONTEXT.md's Approval entry

## What it came to

Four guards, each mutation-tested by deleting it and watching a test go red.

**The operator answering withdraws the patch.** `_stand_down` now calls a new
`Database.clear_dag_interruption(task_id)` beside the `cancel_outbound_for`
that was already there, and says so in the same log line. Only the
`interruption` column is cleared: `paused_at_node` and `paused_question` are
the record of what the run stopped on, and that stays true after the decision
is moot. What has to go is the state a resume would run *from*.

**Three checks before anything resumes**, in `decide_pending_action`, in that
order: the row still carries an interruption; the task is still in
`needs_human`, the only state a paused run leaves it in and the only one a
decision can move it out of; and the parameters still fingerprint to what the
paused run was computed against. Order matters — resuming is what executes
the tool, so every check that can refuse has to come first.

That last one is D7 applied to the one place that reused a stored fingerprint
without re-checking it. It works because both ends fingerprint the same
thing: `prepare` writes `{**old, **asdict(filled)}` back, `_fingerprint` drops
empty values, and the two dicts have the same keys, so the merged params and
prepare's own output hash identically. The ticket-07 resume test passing
unchanged is the evidence — a real approve still goes through.

**The docstring that argued none of this was needed is gone**, replaced by
what is actually true. Its claim was that `dag_state` is replaced whole on
every checkpoint, so any later pass would have overwritten the interruption
and finding one still set proved nothing had touched the task. A pass whose
node 0 returns an `Ask` records nothing and returns — it can run, move the
task, and leave the approval sitting there. That was the load-bearing error:
the reasoning was written down, read as settled, and wrong.

**`harness.resume` no longer raises past its callers.** `(item,) =
state.get_interruptions()` sat outside `_settle`'s try, so the one rule the
module exists to enforce — a failure is `None` and a `last_error`, never an
exception — did not reach it. Two `apply_fix` calls in one turn is ordinary
parallel tool calling, and it wedged the task permanently with the row
intact. It now refuses the same way every other harness failure does, and
approves nothing: one approval cannot say which of two calls it meant.

638 tests pass (634 before, +4).
