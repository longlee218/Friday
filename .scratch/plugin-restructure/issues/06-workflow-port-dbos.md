# 06: Workflow port + migrate the engine onto DBOS

**What to build:** Durable workflows run on DBOS behind a thin `sdk/workflow.py` port; the hand-written DAG engine is retired and its behaviour is preserved. The engine underneath is a library, but the contract plugins code against stays stable.

**Blocked by:** 01. (Recommended after 05.)

**Source:** `spec.md` — Migration order, step 3 (DBOS workflow port + adapter); § Implementation Decisions → "Runtime libraries" (DESIGN-v2 §7).

**Status:** ready-for-agent

- [ ] `sdk/workflow.py` is a thin Friday port (`Node`/`Step`/`Edge`, envelope, `Ask`/`Reply`/`HandOver`)
- [ ] DBOS is the adapter beneath the kernel; nothing outside the adapter imports `dbos`
- [ ] DBOS runs on its own SQLite system-database file (`system_database_url = sqlite:///…`), separate from Friday's application database
- [ ] The derived `DAG.version` source-digest scheme is removed; recovery is DBOS's (application version + per-step memoization)
- [ ] A workflow's input is a serializable scope key; `Deps` are rebuilt inside the run
- [ ] The kernel chain still wraps each step (budget, recording, redaction, `needs`/`side_effect`)
- [ ] Behaviour preserved: the S1 message-path slices and the `api_issue` replay eval stay green
- [ ] Real-DBOS test on SQLite: kill mid-workflow → restart → resume from the last incomplete step
- [ ] `uv run pytest -q` passes

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
  `dbos` (ast guard added, Rule-13 checked). Compiles a `DAG` onto a
  `@DBOS.workflow` walk + per-node `@DBOS.step`; the kernel chain
  (clock/retry/redaction/`node_runs` record) is ported into `_invoke`, not
  delegated to DBOS; `Deps` rebuilt inside the run from a serializable scope
  key; `Ask`/`HandOver` suspend on `recv_async`.
- `tests/test_workflow_port.py` — S3 on real DBOS (throwaway SQLite): linear,
  conditional routing, error→scrubbed envelope + `node_runs`, deps-from-scope-
  key, Ask-suspend-resume, and **box 8** (kill mid-workflow via in-process
  `destroy()`+`launch()` on the same file → recovery resumes, first step
  memoized, not re-run).

**Not ticked — the cutover (slice 3b) remains:** wire `pool.py`/`run_agent.py`
onto the adapter and Friday's own separate system-DB file; move the `api_issue`
graph onto the sdk port; map `Ask`/`HandOver` to the pool's task state machine
(reporter question, `NEEDS_HUMAN`) via `send`/`recv`; retire `friday/dag/` and
drop the `dag_version` column; keep the S1 slices + `api_issue` replay eval
green through it (box 7). No box is fully satisfied until then, so all stay
unticked. `dbos` is pinned in `pyproject.toml`/`uv.lock`.
