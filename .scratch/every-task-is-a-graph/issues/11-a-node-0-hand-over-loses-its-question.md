# 11: A node-0 hand-over loses the question it stopped on

**What to build:** When node 0 hands over, the specific reason it gives
reaches the operator's help-wanted message, the way a later node's reason
already does. Today it is written to the row and then filtered back out.

**Blocked by:** None (can start immediately)

**Decisions:** D7, D8, D14

**Status:** done

## Why

Ticket 04 said node 0 deciding the answer "is not special — a one-node
graph's only node is node 0 — so it gets the same pause-recording treatment
as any later node's `HandOver` does." It records it. The operator never sees
it.

The two ends of the round trip disagree about which parameters the pause was
computed against:

- `friday/dag/prepare.py`'s node writes merged parameters back with
  `set_task_params(merged)` **before** it returns the problem. The database
  now holds the *new* params.
- `friday/tasks/pool.py`'s `_run_dag` then records the pause with
  `fingerprint=_fingerprint(task.params)` — `task` is the object it was
  handed at the start of the pass, so this is the *old* params.
- `_raise_hands`, in the same pass, refetches through
  `tasks_in_state(NEEDS_HUMAN, ...)` and keys `dag_pauses` on
  `_fingerprint(task.params)` of the *new* params.
- `db.dag_pauses` deliberately drops any row whose stored fingerprint differs
  — "a pause computed against different ones is not returned" — so the row is
  filtered out and `_stuck` renders the task's bare type and parameters.

Fifteen lines further down, the same function gets this right for every later
node: `fingerprint = _fingerprint(_prepare_material(prepared))` — prepare's
*output*, which is what D7 says the fingerprint is on. Only the node-0 branch
uses the stale pre-pass snapshot.

It bites whenever extraction filled in anything at all, which is the ordinary
case, and it bites hardest on the one-node graphs D1 created —
`access_request` and `doc_question` — because for them node 0 is the *only*
node, so this is the only reason they can ever produce.

Observed (throwaway repro during review): the row holds
`('prepare', 'no workflow for access_request yet')` and the operator is sent
`access_request #1 — permission: read, project: atlas, summary: …` with no
reason attached.

Related and worth fixing in the same pass: that stored reason —
`plan_by_required_parameters`'s `HandOver(f"no workflow for {task_type} yet")`
— is itself false since ticket 04 gave every type a graph, and uses the
vocabulary ticket 09 retired. It is quoted to the operator verbatim
(CONTEXT.md, *Hand-over*), so it should say what actually happened: the
parameters are complete and there is nothing further to do.

## Acceptance criteria

- [x] A node-0 hand-over's reason reaches the operator's help-wanted message
- [x] The fingerprint a node-0 pause is stored under is computed from the same
      thing every other pause uses, so the two ends cannot disagree again
- [x] Driven at the pool's `run_once` seam: a task whose extraction fills a
      parameter, then hands over at node 0, and the queued help-wanted row
      carries the reason
- [x] The test is watched go red against the current code before the fix
- [x] `plan_by_required_parameters`'s reason no longer says "no workflow …
      yet", and says what is actually true

## What it came to

`_run_dag`'s node-0 hand-over branch fingerprints
`await self._params_now(task)` instead of `task.params`. `_params_now` reads
the row back: node 0 writes what it filled in and *then* returns, so the
snapshot this pass was handed is one write out of date at exactly the moment
the pause is recorded. Every reader keys on what is stored, so the writer has
to as well.

Refetching rather than threading the filled params out of the node keeps the
node's contract — a node returns its result, not its result plus bookkeeping
— and costs one query on a path that is rare by construction. It falls back
to the snapshot if the row has gone, which is no worse than what it replaced.

The false sentence went with it. `plan_by_required_parameters` returned
`HandOver(f"no workflow for {task_type} yet")` — quoted to the operator
verbatim, untrue since ticket 04 gave every type a graph (this *is* that
type's graph, running), and phrased in the vocabulary ticket 09 retired. It
now says what happened: everything needed is here, there is no investigation
past this point, over to you.

The new test uses the **real** `prepare_node` with a stubbed extractor, which
is the whole reason it catches what the existing pause tests missed: those
build a synthetic node 0, and a synthetic node 0 never writes params back, so
the two fingerprints agreed by accident and the round trip looked fine.

One existing test asserted the old wording. It reads the row through
`db.dag_pause`, which has no fingerprint filter — which is precisely why it
stayed green while the operator-facing path was broken, and it now carries a
line saying so and pointing at the test that does go through the filter.

634 tests pass (633 before, +1).
