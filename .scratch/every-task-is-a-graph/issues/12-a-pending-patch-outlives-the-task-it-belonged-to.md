# 12: A pending patch outlives the task it belonged to

**What to build:** A patch waiting on the operator is withdrawn when the work
it belongs to ends — the same way a queued reply already is. Approving one
afterwards neither runs the tool nor raises in the operator's face.

**Blocked by:** None (can start immediately)

**Decisions:** D15, and the "the operator's own message ends the work" rule

**Status:** ready-for-agent

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

- [ ] `_stand_down` clears a pending interruption along with the queued rows,
      so approving after the operator answered does nothing rather than
      applying a patch and raising
- [ ] `decide_pending_action` refuses on a task that is no longer in a state
      where the decision means anything, before it resumes the run
- [ ] Resuming re-checks that the stored state still matches the task's
      current parameters, or the docstring that promises that check is
      corrected to say what actually happens
- [ ] A run paused on more than one approval fails the way every other
      harness failure does — `last_error` and a hand-over — not an unhandled
      `ValueError`
- [ ] Each guard added is deleted once and watched go red
- [ ] Whatever is decided about staleness is written down where the next
      reader looks: the docstring, or CONTEXT.md's Approval entry
