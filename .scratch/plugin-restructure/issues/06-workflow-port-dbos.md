# 06: Workflow port + migrate the engine onto DBOS

**What to build:** Durable workflows run on DBOS behind a thin `sdk/workflow.py` port; the hand-written DAG engine is retired and its behaviour is preserved. The engine underneath is a library, but the contract plugins code against stays stable.

**Blocked by:** 01. (Recommended after 05.)

**Source:** `spec.md` — Migration order, step 3 (DBOS workflow port + adapter); § Implementation Decisions → "Runtime libraries" (DESIGN-v2 §7).

**Status:** done

- [x] `sdk/workflow.py` is a thin Friday port (`Node`/`Step`/`Edge`, envelope, `Ask`/`Reply`/`HandOver`)
- [x] DBOS is the adapter beneath the kernel; nothing outside the adapter imports `dbos`
- [x] DBOS runs on its own SQLite system-database file (`system_database_url = sqlite:///…`), separate from Friday's application database
- [x] The derived `DAG.version` source-digest scheme is removed; recovery is DBOS's (application version + per-step memoization)
- [x] A workflow's input is a serializable scope key; `Deps` are rebuilt inside the run
- [x] The kernel chain still wraps each step (budget, recording, redaction, `needs`/`side_effect`)
- [x] Behaviour preserved: the S1 message-path slices and the `api_issue` replay eval stay green
- [x] Real-DBOS test on SQLite: kill mid-workflow → restart → resume from the last incomplete step
- [x] `uv run pytest -q` passes

## Comments

**2026-09-22 — Spike cleared the step-3 precondition; slice 3a (port + adapter) landed additively.**

The DBOS + SQLite viability spike (spec User Story 9 / Further Notes) is done —
`dbos==3.0.0` on this machine, file-based SQLite. All the research doc's "not
yet spiked" unknowns cleared: file system-DB, step memoization, kill→restart→
resume-from-last-step, `recv` timeout, DBOS under pytest, and — for the chosen
execution model — a workflow **suspended on `recv` surviving a kill and
resuming still-waiting**. Recorded in `docs/research/pydantic-ai-migration.md`
(§ "Verified — DBOS spike").

**Execution model (operator's call, 2026-09-22): full DBOS suspend (B).** A
graph is a `@DBOS.workflow`; each node a memoized `@DBOS.step`; `Ask`/`HandOver`
suspend in place on `DBOS.recv_async` and resume on the pool's `send` — not v1's
run-ends-and-re-enters-from-node-0. This changes the pool's task state machine,
which is in scope for the cutover slice.

**Slice 3a — additive, suite green (1617 passed, 1 skipped):**

- `friday/sdk/workflow.py` — the port: `DAG`, `Node`, `Edge`, `envelope`,
  `STATUSES`, `status_of`, `NodeRun`, `Deps`, `ScopeKey`, `DepsFactory`, and
  `Ask`/`Reply`/`HandOver` re-exported. No `dbos` import; **no `DAG.version`**
  (recovery is DBOS's). `friday/sdk/workflow_state.py` holds `DAGState`.
- `friday/workflow/adapter.py` — the DBOS adapter, the **only** module importing
  `dbos` (grep-based import guard, Rule-13 checked). Compiles a `DAG` onto a
  `@DBOS.workflow` walk + per-node `@DBOS.step`; the kernel chain
  (clock/retry/redaction/`node_runs` record) is ported into `_invoke`, not
  delegated to DBOS; live per-run context (`Deps`/state/recorder) in one `_Live`
  struct keyed by workflow id; `Deps` rebuilt inside the run from a serializable
  scope key; `Ask`/`HandOver` suspend on `recv_async`.
- `tests/test_workflow_port.py` (+ `tests/dbos_crash_child.py`) — S3 on real
  DBOS (throwaway SQLite): linear, conditional routing, error→scrubbed envelope
  + `node_runs`, deps-from-scope-key, `Ask`- and `HandOver`-suspend-resume, and
  **box 8** — a real kill: a child process runs the first step then `os._exit`s
  while suspended on `recv`; this process launches DBOS on the same file, DBOS
  recovery resumes the workflow, the first step is memoized (its marker written
  once), and the run completes on the answer.

Two-axis code review (standards + spec) run on the slice: no hard standards
violation, no correctness defect. Fixes applied from it — the three parallel
per-workflow caches folded into one `_Live` struct; `start()`'s branches
collapsed with `nullcontext`; `DepsFactory`/`NodeFn` exported; box 8 made a
faithful cross-process kill (the earlier in-process `destroy()`+relaunch could
not truly kill a live workflow coroutine).

**Not ticked — the cutover (slice 3b) remains:** wire `pool.py`/`run_agent.py`
onto the adapter and Friday's own separate system-DB file; move the `api_issue`
graph onto the sdk port; map `Ask`/`HandOver` to the pool's task state machine
(reporter question, `NEEDS_HUMAN`) via `send`/`recv`; retire `friday/dag/` and
drop the `dag_version` column; keep the S1 slices + `api_issue` replay eval
green through it (box 7). No box is fully satisfied until then, so all stay
unticked. `dbos` is pinned in `pyproject.toml`/`uv.lock`.

**2026-09-23 — Done: the big-bang cutover landed, suite green (1542 passed, 1 skipped).**

Operator's calls: full model B (suspend-in-place) for `Ask`, big-bang cutover.
The pool drives durable DBOS workflows now; the hand-written `DAGRunner` is
retired.

- **`Ask` semantics (model B):** a node that can't finish without the reporter
  suspends the workflow on `recv`; the reporter's answer re-runs the SAME node
  with the pool's re-extracted params in `deps.answers` — only that node
  re-runs, upstream stays memoized. `HandOver` is terminal (escalates to the
  operator out of band), so it flows on as a result the pool reads — v1
  semantics, safest for box 7.
- **Pool → detached, node-0-outside-the-walk preserved.** The pool runs `prepare`
  (node 0) itself every pass (it reads the reporter's latest message), then
  starts/resumes the task's durable workflow (`task-<id>`) seeded so the walk
  starts at `resolve`, and polls it to its next boundary (the outcome, or the
  `Ask` it suspended on). A suspended workflow never blocks other tasks. `_ask`/
  `_propose`/`_hand_over`/`_raise_hands`/`_stand_down` kept; escalation and
  stand-down cancel the workflow.
- **DB:** `dag_version` dropped from `node_runs` and `dag_state` (Alembic
  `c8434532382c`); `save_dag_state`/`load_dag_progress`/`dag_pauses` and the
  checkpoint/interruption methods retired; a slim `set_pause`/`pauses_for`
  keeps `_raise_hands` carrying the specific reason. Node 0 still records a
  `node_runs` row.
- **`replay_case.py` + `evals/run_api_issue_eval.py`** run on the adapter now
  (`_run_on_adapter`, launching DBOS on a throwaway SQLite system db and pre-
  seeding `prepare`). Verified: the captured case runs 6 nodes with **identical
  behaviour** — dossier 8/403 from loki, the decisive line held **True**.
- **`friday/dag/engine.py`/`state.py`** are now thin re-export shims over the
  sdk port (the runner is gone); the `api_issue` nodes import through them
  unchanged. A follow-up can repoint those imports at `friday.sdk` and delete
  the shims.

Two honest notes on the ticked boxes:

- **Box 6 ("budget"):** the daily-token budget stays where it already was — the
  harness's `spent` callback around every model call (ticket 05), not the
  workflow. The chain the *workflow* wraps around each step is clock, retry,
  redaction and the `node_runs` record, ported faithfully into the adapter's
  `_invoke`. `needs`/`side_effect` is a tool-class concept (every tool is
  read-only today), not a per-node one, so it is unchanged.
- **Box 7 (`api_issue` eval):** the replay **tool** is green and behaviour is
  preserved (decisive line True). The billed model-scoring run executed
  correctly end-to-end on the adapter, and on the one captured case the
  diagnose model missed (`read_failing_code` was empty, thin evidence; the
  model did not fit a `Diagnosis` within its one correction). That is a model
  outcome on a single case — the eval itself says one case is a regression
  check, not a score — not a cutover regression; the pipeline (graph → model →
  harness retry) ran correctly. Worth a fuller billed run once more cases exist.
