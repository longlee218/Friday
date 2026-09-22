# Plugin restructure (microkernel) — spec

**Status:** ready-for-agent
**Board:** plugin-restructure
**Source:** `docs/DESIGN-v2.md` (accepted target 2026-09-22, Appendix D),
`docs/adr/0001-runtime-libraries-before-plugin-migration.md`, grilling
session 2026-09-22.

This spec is the executable plan for the re-sequenced migration in
DESIGN-v2 §15. `/to-tickets` splits it into one issue per §15 step, worked
blockers-first. `docs/DESIGN.md` stays the as-built record; each step moves
its section across as it lands.

---

## Problem Statement

From the operator's perspective:

- Friday's capabilities are wired into the core by literal registries — the
  task-type params/extractor/graph maps, the memory reader/writer maps, the
  sender map. Adding a task type, a source, a memory kind, or a second
  persona means editing the core, and two pieces of work cannot proceed in
  parallel without colliding in the same core files.
- The core stands on a hand-written DAG engine and a hand-wired
  `openai-agents` harness — code the operator maintains instead of leaning
  on a maintained library, and code that will be replaced (DBOS, Pydantic
  AI), so anything built tightly against it is built twice.
- The running system has real defects: an approved reply can post twice
  across a crash; the board writes `admin` memory with no Host/Origin/CSRF
  check and a `BOARD_TOKEN` that gates nothing; the approver's identity is
  never verified; two agent processes can run at once.

## Solution

From the operator's perspective:

- A small **kernel** owns the safety invariants; every task type, tool,
  source, memory kind and skill registers as an **in-repo plugin** through
  one `register(api)` entrypoint. The operator can develop a plugin in a
  scratch location (own tests, eval, config) and copy or register it in when
  it is ready, without touching the core.
- The hand-written engine and harness are replaced by **DBOS** (durable
  workflows) and **Pydantic AI** (agent loop) as the **foundation, adopted
  before the plugin split**, each behind a Friday-owned seam so a plugin
  never imports the library directly.
- Migration order (re-sequenced, ADR 0001): library-independent defect
  fixes → Pydantic AI → DBOS → task types and memory kinds register
  themselves → the `sdk`/`kernel`/`plugins` split → a second persona proves
  the split holds. Triage-at-scale, the Channel/Approval split, principals
  and packaging stay deferred behind their triggers.
- DBOS runs locally on its **own SQLite system-database file**, and its
  workflows are **observable in Friday's existing board** — no new service.

## User Stories

1. As the operator, I want the board to reject any write that lacks the
   session's CSRF token or carries a foreign Host/Origin, so that a page in
   another tab cannot write `admin` memory into Friday.
2. As the operator, I want `BOARD_TOKEN` removed, so that no config switch
   can expose a writable board to the LAN.
3. As the operator, I want the identity of whoever approves a reply checked
   against `operator_id`, so that only I can release a message that speaks
   in my name.
4. As the operator, I want a second `run_agent` process to refuse to start
   while one holds the lock, so that two outbox loops never run at once.
5. As the operator, I want `busy_timeout` set on SQLite, so that a
   concurrent read from the board does not fail a write.
6. As the operator, I want the agent loop to run on Pydantic AI, so that I
   maintain my invariants and glue, not a hand-wired harness.
7. As the operator, I want a `ModelProvider`/harness seam drawn as part of
   the Pydantic AI adoption, so that swapping or adding a provider later is
   an adapter, not a rewrite.
8. As the operator, I want the kernel to still enforce budget, redaction and
   recording around every model call, so that moving to Pydantic AI does not
   move a safety invariant into the library.
9. As the operator, I want a DBOS spike on MiniMax-M3 + SQLite before any
   engine change, so that I do not migrate onto a foundation that cannot run
   on this machine.
10. As the operator, I want durable workflows to run on DBOS behind a thin
    `sdk/workflow.py` port, so that the engine underneath is a library while
    the contract plugins code against stays stable.
11. As a plugin author, I want to express a task-type graph in Friday's own
    workflow types and never import `dbos`, so that a DBOS version change
    does not force me to rewrite my plugin.
12. As the operator, I want DBOS to use its own SQLite system-database file,
    separate from Friday's application database, so that workflow state and
    domain state stay clearly separated.
13. As the operator, I want the outbox delivery loop to run as a DBOS
    workflow step with an idempotency key, so that a crash mid-send never
    double-posts and never silently loses an approved reply.
14. As the operator, I want a send whose outcome is unknown to land in
    `delivery_unknown` and wait for me, never auto-retried, so that a
    channel without an idempotency key still cannot double-post.
15. As the operator, I want the derived `DAG.version` source-digest scheme
    dropped in favour of DBOS application-version + step memoization, so that
    recovery resumes from the last incomplete step instead of re-running.
16. As the operator, I want workflow inputs to be a serializable scope key
    with `Deps` rebuilt inside the run, so that DBOS can persist a workflow
    without trying to serialize a live source or draft handle.
17. As the operator, I want to watch running, queued, succeeded and failed
    workflows in the existing board, so that I can see what Friday is doing
    without a separate dashboard.
18. As a plugin author, I want to register a task type (params, extractor,
    graph, deps, needs) through `register(api)`, so that adding a task type
    is adding a plugin, not editing the router.
19. As a plugin author, I want to register memory kinds through
    `register(api)`, so that a pack kind ships with its plugin.
20. As the operator, I want `MemoryKindSpec` to carry only the fields the
    registry actually uses today, so that no inert, untested field pretends
    to be a rule.
21. As a plugin author, I want the kernel to import no plugin and name no
    task type or pack-kind literal, so that my plugin is genuinely
    detachable.
22. As the operator, I want `devops` (the `api_issue` work) extracted into a
    plugin against the `sdk` ports, so that the split is proven on the real
    task type, not a toy.
23. As the operator, I want a second persona (`docs`, `doc_question`) added
    with zero kernel diff, so that I know a new persona needs no core edit.
24. As the operator, I want the triage prompt untouched by the registry
    steps, so that classifier accuracy does not move when wiring moves.
25. As the operator, I want the triage eval re-run and reported whenever a
    step touches the harness or the enabled task-type set, so that I never
    ship a wiring change that quietly broke the classifier.
26. As the operator, I want the current hard-coded `api_issue` fallback kept
    and triage-at-scale deferred, so that I do not build per-scope label
    machinery for one real task type.
27. As the operator, I want the store split behind the `Database` facade with
    workflow state left to DBOS, so that kernel invariants leave `db.py`
    without re-splitting what DBOS owns.
28. As the operator, I want the daily backup to cover both SQLite files, so
    that a restore brings back domain state and workflow state together.
29. As the operator, I want every migration step to leave the whole suite
    green, so that the restructure never trades a working system for a
    half-built one.
30. As the operator, I want each step to move its section from DESIGN-v2 into
    DESIGN.md, so that the as-built record stays true as the target is built.

## Implementation Decisions

**Shape and trust**
- The kernel owns the invariants and names no plugin. `friday/sdk` holds
  contracts only (Protocols + dataclasses); `friday/kernel` imports `sdk`; a
  plugin imports `sdk` only. A plugin is a `Plugin` value + a `register(api)`
  function (no base class), named by import path in `config.yaml`.
- Plugins land in the repo. "Develop elsewhere, copy/register in" is a
  workflow, not an architecture requirement — no entry-point discovery,
  per-plugin packaging, `friday-sdk` distribution or `friday.lock` in scope.

**Runtime libraries as the foundation (before the split)**
- **Pydantic AI** replaces `openai-agents` in the harness and draws the
  `ModelProvider`/harness seam (DESIGN-v2 §6.7, no longer deferred). Pydantic
  AI owns the agent loop; the kernel chain wraps budget, redaction and
  recording around every model call.
- **DBOS** replaces the hand-written DAG engine behind a thin
  `sdk/workflow.py` port (Friday's own `Node`/`Step`/`Edge`/envelope /
  `Ask`/`Reply`/`HandOver`). DBOS is the adapter beneath the kernel; plugins
  never import `dbos`. DBOS and Pydantic AI are foundations the kernel builds
  on directly (not swappable-behind-a-port), and the kernel re-asserts each
  §3.3 invariant in its own chain around every step.
- DBOS runs on its **own SQLite system-database file**
  (`system_database_url = sqlite:///…`), separate from Friday's application
  database. The two SQLite files cannot share one transaction, so
  double-post protection comes from **DBOS exactly-once step memoization +
  an idempotency key on the channel send**, with `delivery_unknown` as the
  fallback for a channel that has no idempotency key. Per DBOS docs a system
  DB and an application DB must be the same type (both SQLite here).
- The derived `DAG.version` source-digest scheme is dropped; recovery is
  DBOS's (application version + per-step memoization, resume from the last
  incomplete step). A workflow's input is a **serializable scope key**;
  `Deps` (live handles) are rebuilt inside the run.

**Registration and scope**
- Task types register through `TaskTypeSpec` (params, extractor, graph in the
  `sdk` workflow types, deps, needs); memory kinds through `MemoryKindSpec`,
  **trimmed to the fields the registry uses** (name, data, writers,
  cardinality, injected). `schema_version`/`upgrade`/`sensitivity`/
  `allowed_scopes`/`audience`/provider-policy are added on trigger with a
  test.
- Triage-at-scale (per-scope label set, `core:intake`, example
  provenance/retirement) is deferred; the hard-coded `api_issue` fallback
  stays.

**Defects, split by "is it a durable loop?"**
- Ship immediately, library-independent: board protection (delete
  `BOARD_TOKEN`; exact Host/Origin, startup session secret, CSRF on every
  write), approver-id checked against `operator_id`, single-instance lock,
  `busy_timeout`.
- The outbox delivery state machine (`dispatching`/`delivery_unknown`,
  idempotency key, frozen-payload hash, `policy_approved`) folds into the
  DBOS phase as a workflow.

**Observability (reuse before rewrite)**
- Extend the existing board (React + Vite SPA, FastAPI, SSE Monitor) with a
  workflow panel fed by `DBOSClient.list_workflows(...)` + per-workflow
  progress events. No DBOS Conductor.

**Local operation (§12.1)**
- One single-instance lock (one process owns both SQLite files); the daily
  backup covers both files; WAL on both; startup recovery = DBOS resumes
  PENDING workflows + Friday's inbox sweep.

**Migration order (DESIGN-v2 §15, re-sequenced):** 0 clean tree → 1
library-independent defects → 2 Pydantic AI + `ModelProvider` seam → 3 DBOS
workflow port + adapter (folds the outbox loop) → 4 task types register
themselves → 5 memory kinds register themselves (trimmed spec) → 6 typed
per-run `Deps` from the scope key → 7 `sdk/` + `plugins/devops/` → 8 second
persona `plugins/docs/` → 9 store split (workflow state excluded) → 10
remaining "now" §12 rows.

## Testing Decisions

A good test asserts external behaviour, not implementation: the message that
went out, the row's state after a crash, the workflow that resumed — not
which private function was called. Every guard is deleted once and watched go
red (Rule 13). Four seams, existing ones preferred, at the highest point:

- **S1 — behaviour (existing, unchanged assertions).** The message-path
  slices (`test_pool.py`, `test_message_flow.py`, `test_outbox.py`,
  `test_dag.py`, driven by `FakeProvider` + the scripted harness) and the
  `api_issue` replay eval (`replay_case.py` with `CannedReads`/
  `CannedKubectl` doubles) must stay green through **every** migration step.
  No new behaviour seam is added per step; every step leans on S1.
- **S2 — model/provider (existing seam, new double).** Replace the vendored
  `ScriptedModel` with a Pydantic-AI test double at the harness seam
  (`test_harness.py` as prior art), so structured-output validation and the
  one correction turn run on the real inherited code.
- **S3 — workflow port (new, highest seam).** Test against **real DBOS** on a
  throwaway SQLite system-database file (as the store tests already use real
  SQLite): crash-between-channel-call-and-write → `delivery_unknown` after
  restart, nothing sent twice; PENDING-workflow resume. Individual nodes stay
  unit-testable with stub deps for speed (`test_tools.py`,
  `test_investigate_tools.py` as prior art).
- **S4 — structure (new, ast).** A dependency-rule import-graph test over
  `friday/sdk`, `friday/kernel`, `plugins/*` (sdk imports nothing of ours;
  kernel imports sdk; a plugin imports sdk only), plus "kernel names no
  plugin" (no plugin import, no task-type/pack-kind literal). Extends the
  `test_sources_are_the_only_door.py` / `test_composition_root.py` ast
  pattern.
- **Triage eval gate.** `evals/run_triage_eval.py` on the enabled set at step
  2 (harness changed) and step 7 (task-type set changed), reporting accuracy,
  confusion matrix and threshold table (CLAUDE.md rule 4).

Modules tested: the board write path (S-guards), the harness (S2), the
workflow port + outbox (S3), the registry/dependency rules (S4), the
`devops` and `docs` plugins (through S1 behaviour), the store facade after
the split.

## Out of Scope

- Triage-across-plugins, `core:intake`, per-scope label sets (deferred).
- Channel/Approval split, a second channel, Slack/email, world/room/entity.
- `Store` Protocol, Postgres, a second store adapter.
- Principals/roles/board accounts beyond the single operator; `team` scope
  level; the user audience axis (types defined in `sdk`, columns on trigger).
- Packaging: entry-point discovery, a separate `friday-sdk` distribution,
  `friday.lock`, plugins living outside the repo.
- MCP sandbox, `egress` tool class, per-tool rate limits.
- DBOS Conductor (the client API + the existing board cover observability).
- Jev model adoption; a model planning a workflow.

## Further Notes

- **DBOS + SQLite viability is unverified until the spike (step 3
  precondition).** DBOS Python is documented Postgres-first but supports a
  SQLite system database (`system_database_url = sqlite:///…`); the Rust port
  documents SQLite explicitly. If the spike on MiniMax-M3 + this machine's
  SQLite fails, step 3 is re-planned before anything downstream proceeds.
- `docs/DESIGN.md` remains authoritative for what exists; where DESIGN-v2's
  body and its Appendix D disagree, Appendix D and ADR 0001 win, and the body
  is corrected as each step lands.
- New target vocabulary (kernel, trusted adapter, contribution plugin,
  handle, toolset, core/pack kind, scope/audience, principal) lives in
  DESIGN-v2 §17 and moves into `CONTEXT.md` as each term becomes real in the
  code.
