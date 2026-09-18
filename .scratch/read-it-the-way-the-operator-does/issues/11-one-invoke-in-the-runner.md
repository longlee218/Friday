# 11: One invoke in the runner

**What to build:** `DAGRunner._invoke` — per-node timeout, per-node retry
with an explicit exception list, exception → envelope, a `node_runs` row per
attempt — the `status` envelope, and `dag_version` in the checkpoint key.

**Blocked by:** nothing. **Decisions:** spec, "Architecture v3"; finding E.
**Status:** done

## Verify
- Whole suite. A node that hangs returns `timed_out` and the run reaches its
  last node; a node raising a listed exception retries, an unlisted one does
  not; a renamed node does not inherit a stored result.
- Config load refuses a model node whose timeout is below its harness's.

## Done

`friday/dag/engine.py`: `Node` gains `timeout_seconds`, `retry_on`,
`max_attempts`, `retry_backoff_seconds` and `agent`; `DAGRunner._invoke` runs
every node under one deadline (backoff included, cut to what is left), retries
only the listed exceptions with doubling backoff, turns an expired clock into
`{status: timed_out}` and any other exception into `{status: error}` (a
`TimeoutError` the node raises itself is an error, not the clock), and hands
every attempt to `on_node_run`. `envelope`/`status_of`/`STATUSES` are the
envelope. `DAG.version` is a digest of entry, node names and edges — derived,
not declared, so nobody has to remember to bump it.

`friday/tasks/pool.py`: node 0 goes through the same invoke (`run_entry`), so
the one-node prepare graph gets a `node_runs` row and otherwise behaves as
before — one visible difference: its hand-over on an exception reads
`"<type> failed: RuntimeError: boom"` where it read `"<type> failed: boom"`.
Failed envelopes are not checkpointed (a failed node runs again next pass, as
a raising one did), and a graph ending on one hands over naming the node and
the reason. `dag_version` is in the checkpoint key (`dag_state.dag_version`,
required on `save_dag_state`). New table `node_runs`, migration
`37048e489fc1`. `friday/dag/router.py`: `check_node_clocks`, called from
`register_dags`, refuses a model node whose timeout is under its agent's
`timeout_seconds + NODE_CLOCK_MARGIN_SECONDS` (5s), or whose agent is not
configured.

Tests: `tests/test_dag_invoke.py` (20). Suite: `1 failed, 1261 passed, 1
skipped` — the one failure is the known baseline
`test_doc_paths_resolve_to_existing_files`. Guards deleted once and watched go
red (15): the clock, the retry list both ways, own-clock-only, empty
`retry_on` refused, sink per attempt, failing sink, deadline cutting the
backoff, `dag_version` in `_dag_row`, failures not checkpointed, hand-over
naming the failure, `check_node_clocks` call, the margin, node 0 through the
invoke. The backoff guard passed with its first test and the test was
tightened until it did not.

Not done here: no subagent `code-review` ran inside this ticket's run (the
implementing agent could not spawn one) — owed by the orchestrator.

## Review fixes

A review of 97c0bd7 found one must-fix and two spec gaps; all three closed.

- **Exception text reached the task DB unscrubbed.** `_invoke` builds the
  reason from the exception, and from there it was a `node_runs.reason`, a
  hand-over, and `dag_state.paused_question` — none of which existed before
  this ticket for a raising node. Scrubbed where it is built (`_invoke`) and
  again at both writes (`record_node_run`, `save_dag_state`'s
  `paused_question`), the second for the reason `fail_outbound` scrubs its
  own: the rule is only unconditional at the point of writing.
- **Node 0 was the model node the clock check never saw.** It runs the
  extractor but declared no agent and no timeout. `build_simple_dag` now
  gives it `agent="extractor"` and a clock of the extractor's
  `timeout_seconds + NODE_CLOCK_MARGIN_SECONDS`, derived so raising the
  extractor's timeout moves node 0's with it. No `extractor` block: no agent,
  no clock, as before.
- **"At config load", not at registration.** `router.check_graphs(config)`
  builds the graphs from the configuration alone and checks the clocks;
  `run_agent.py` calls it straight after `load_config`, before the database
  is opened. `register_dags` still checks what it registers.

Tests: 7 new in `tests/test_dag_invoke.py` (scrub at the invoke, scrub at
the store, token end to end through the pool; node 0's agent and clock ×2,
node 0 with no extractor, `check_graphs` from config alone) and 1 in
`tests/test_composition_root.py` (`check_graphs` before `Database.connect`).
Guards deleted once and watched go red (8): the invoke's scrub, the
`record_node_run` scrub, the `paused_question` scrub, node 0's agent, node
0's margin, the no-extractor branch, `check_graphs`'s check, and its call in
`run_agent.py`. Suite: `1 failed, 1269 passed, 1 skipped` — the one failure
is the known baseline `test_doc_paths_resolve_to_existing_files`.

## Docs owed

`CLAUDE.md` (not edited, per the operator's rewrite in another checkout):
- Architecture constraints, "Workflows are deterministic Python": durable
  resume now also discards state whose `DAG.version` differs; every node,
  node 0 included, runs through `DAGRunner._invoke` (timeout, explicit retry
  list, exception → `{status: error}` envelope, one `node_runs` row per
  attempt).
- "Every model call is bounded": a model node's own timeout must outlast its
  agent's `timeout_seconds` by `NODE_CLOCK_MARGIN_SECONDS`, refused at
  `register_dags` (finding E, two clocks).
- Layout, `friday/dag/`: `engine.py` now also holds the invoke and the result
  envelope.
- "Every model call is bounded": node 0 is a model node — its clock is the
  extractor's `timeout_seconds + NODE_CLOCK_MARGIN_SECONDS` — and the clock
  check runs at config load (`check_graphs`, straight after `load_config` in
  `run_agent.py`) as well as at `register_dags`.
- Conventions, the token paragraph: a node's exception text is scrubbed in
  `DAGRunner._invoke` and again where `node_runs.reason` and
  `dag_state.paused_question` are written.
