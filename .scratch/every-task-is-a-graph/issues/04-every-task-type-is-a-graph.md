# 04: Every task type is a graph

**What to build:** `access_request` and `doc_question` run a graph too — one
node — so resume, checkpointing and hand-over behave the same whatever the
task is. The second way of deciding what to do with a task disappears, and
with it the exception a node had to raise to stop.

**Blocked by:** 03

**Decisions:** D1, D9

**Status:** done

## Why

There is no reason `access_request` has no resume other than the branch it
took. A one-node graph is what the deterministic path already is; calling it a
graph costs one registration and deletes a whole route through the loop —
the "is there a graph for this?" question, the `None` answer, the fallback
planner, and every test that exercised the fallback separately.

`PauseForHuman` goes at the same time. It exists so a node can stop and be
resumed at the node that stopped; ticket 03 replaced that with re-running from
node 1 when the params change, which reaches the same place through the
mechanism every task already uses. Leaving the exception beside its
replacement is how a codebase ends up with two of everything — which is what
this whole spec is repairing.

The principle it does not repeal: **do not build multi-node graphs before
there are steps worth skipping.** A one-node graph is that principle under one
name, not an argument against it.

## Acceptance criteria

- [x] `access_request` and `doc_question` each run a one-node graph: prepare,
      then ask for what is missing or hand over
- [x] The router never answers "no graph", and the loop has no branch for the
      absence of one
- [x] A one-node type gets checkpointing, fingerprinting and hand-over on the
      same terms as `api_issue`, pinned by a test that runs one through
      `run_once` twice
- [x] `PauseForHuman` is gone: a node that cannot decide returns an action,
      the run ends, and the task waits
- [x] The specific question a node stopped on still reaches the operator's
      help-wanted message, worded as it is today
- [x] New reporter text re-runs from node 1 under ticket 03's rule, reaching
      the same outcome the paused-node resume gave
- [x] Every assembled prompt is byte-identical before and after, captured

## What it came to

**One shared node 0.** `friday/dag/prepare.py` is new: `prepare_node(task_type,
params_cls, on_ready=None)` builds node 0 for any graph, and D2's "one shared
node" is now literally one function, not one copy per graph. `api_issue`'s own
`_prepare`/`_prepared_ok` (written in ticket 03) moved there unchanged and
`api_issue.py` now calls `prepare_node("api_issue", ApiIssueParams)`. For a
type with nothing past node 0, `on_ready` turns a complete, valid set of
parameters into the graph's own answer — `access_request` and `doc_question`
pass `plan_by_required_parameters` itself as `on_ready`, reusing its exact
`Park(f"no workflow for {task_type} yet")` text rather than deriving it a
second time. `friday/dag/router.py`'s `build_simple_dag` builds these two
graphs; `register_dags` now loops over every entry in `PARAMS` and registers
one for whichever isn't `api_issue`.

**The deterministic path is gone.** `WorkflowRunner._plan` no longer has a
branch for "no graph" — it asserts the invariant instead
(`assert dag is not None, ...`), and `_remember`, and the calls to `prepare`/
`plan_by_required_parameters` that used to run ahead of routing, are deleted
along with it (they're still used, just from inside `prepare_node` now).

**`PauseForHuman` dissolves.** `friday/dag/pause.py` is deleted. `_fix_bug`'s
two stopping cases (a dangerous change, no fixer configured) now `return
Park(...)` instead of raising, folding what used to be `.options` into the
reason text since `Park` has no separate field for them (`.evidence` is
dropped — it was never persisted or surfaced anywhere, so nothing observable
changes). The edge `fix_bug → compose_reply` gained a guard
(`when=_fix_bug_ok`) so a `Park` ends the run there instead of falling through.

**A real gap this surfaced, not created:** node 0 returning a `Park` (the
shape a one-node graph's hand-over always takes) used to bypass the
pause-recording logic entirely — it returned before ever reaching the code
that saved `paused_at_node`/`paused_question`. Fixed by giving `_run_dag` one
shared `_record_pause` helper, called from both the node-0 early-return and
the post-`runner.run()` outcome check. Without this, `access_request` and
`doc_question`'s hand-over would have reached `NEEDS_HUMAN` with no specific
reason attached — exactly the gap ticket 04's own acceptance criteria (test
that runs a one-node type through `run_once` twice, checking `dag_pause`)
exist to catch, and did.

**Tests.** Four generic-engine `PauseForHuman` tests in `test_dag.py` are
deleted outright — the mechanism they tested no longer exists — and replaced
with one (`test_a_node_that_cannot_decide_ends_the_run_where_it_stands`)
showing the replacement shape: an `Ask`/`Park`, guarded by an absent edge, not
a special case. Five pool-integration pause tests became `return Park(...)`
in place of `raise PauseForHuman(...)`, same assertions. Six in
`test_dag_api_issue.py` similarly. New:
`test_a_one_node_type_hands_over_on_the_same_terms_as_api_issue` — the
required "run one through `run_once` twice" test — and
`test_extraction_runs_when_a_message_is_linked` (in `test_workflow_runner.py`)
no longer needs to remove `api_issue`'s graph to reach the extraction path,
because there is only the one path now.

**Docs.** Ticket 09 owns the full CLAUDE.md/CONTEXT.md sweep, but two claims
ticket 04 made outright false rather than merely imprecise were fixed here:
CLAUDE.md's "a task type without a graph is not a mistake... deterministic
path" constraint, and CONTEXT.md's Workflow/Graph sections describing
`PauseForHuman` and the two-path split as if they still existed. Left for
ticket 09: the "graph engine — nodes, edges, checkpointed resume" framing
elsewhere in CLAUDE.md could now name `dag/prepare.py` more prominently once
the package's final shape (ticket 09's pool, D14's `hand_over`) is known.

611 tests, down from 613: four generic `PauseForHuman` tests deleted, one
replacement and one new one-node-type test added (613 − 4 + 2 = 611).
`friday/*/prompt.py` untouched throughout.
