---
Status: accepted (operator, 2026-09-22)
---

# Runtime libraries are the foundation, adopted before the plugin migration

## Context

`DESIGN-v2.md` §15 originally ordered the migration **defects first**, then
the registry / `sdk`–`kernel`–`plugins` split, with the runtime libraries
(Pydantic AI for the agent loop, DBOS for durable workflows) deferred to §16
"after this restructure". A grilling session on 2026-09-22 surfaced that this
order draws the load-bearing seams twice: the plugin-facing contract
(`TaskTypeSpec.graph`, §6.1; the workflow port, §7) is defined against the
hand-written DAG engine and the `openai-agents` harness that DBOS and
Pydantic AI are going to replace. Building plugins against a doomed engine
defeats the whole reason for the split — a *stable* SDK surface plugins can be
developed against in parallel. The operator has committed to DBOS replacing
the DAG engine (evaluated as a good fit, Pydantic-friendly, credible OSS) and
Pydantic AI replacing `openai-agents` (spiked 15/15). The system is not yet
truly in production, so there is little cost to replacing the foundation
first.

## Decision

Adopt the runtime libraries **as the foundation, before the plugin
migration**. Sequence: immediate library-independent defect fixes → Pydantic
AI (harness / `ModelProvider` seam) → DBOS (workflow port + adapter) →
registries and the `sdk`–`kernel`–`plugins` split.

- **`sdk/workflow.py` is a thin Friday-owned port** (its own `Node`/`Step`/
  `Edge` types); DBOS is an **adapter beneath the kernel**; plugins import
  `friday.sdk` and **never import `dbos`**. One seam per outside library
  (Rule 11). This is the only thing that makes the "stable contract" real.
- **DBOS and Pydantic AI are foundations the kernel builds on**, not
  swappable-behind-a-port adapters (no swap-port ceremony for a library we
  have committed to). The kernel still **wraps its §3.3 invariants**
  (budget, redaction, recording) around every step and model call — it does
  not delegate them to the library.
- **Defects are split by "is it a durable loop?":** board protection
  (§6.10), the approver-id check (§3.3), the single-instance lock and
  `busy_timeout` (§12.1) ship immediately, independent of the libraries. The
  **outbox delivery state machine** (`dispatching`/`delivery_unknown`,
  idempotency key, frozen-payload hash) is folded into the DBOS phase,
  because the delivery loop is itself a durable-workflow candidate — writing
  it by hand now would be rewritten into DBOS later.
- **Trimmed to what the migration needs:** triage-across-plugins (§10:
  per-scope label set, `core:intake`, example retirement) is **deferred to
  §16** until the enabled set is actually large; `MemoryKindSpec` (§9.2) is
  trimmed to the fields the registry step uses, the rest added on trigger
  with a test (Rule 13).

## Considered options

- **Defects-first, libraries-after (as §15 was written).** Rejected: the
  outbox delivery machine and the workflow/version scheme get hand-written,
  then rewritten into DBOS; §7's `version = digest of node source` scheme
  conflicts with DBOS step-memoization and app-version recovery.
- **Interleave: registry steps that don't touch the engine first, DBOS in
  the middle, engine-dependent steps last.** Rejected by the operator: since
  the system is not live, do the foundation cleanly first rather than thread
  it through a half-done migration.

## Consequences

- `DESIGN.md` stays authoritative as the as-built record; `DESIGN-v2.md` §15
  is re-sequenced and §6.7 (`ModelProvider`) leaves the §16-deferred set.
- §7's `version = digest of node source` scheme is replaced by DBOS
  app-version + per-step memoization; resume is DBOS's, not a re-run-from-
  entry on a source-digest mismatch.
- `Deps` (§5.2) cannot be serialized (they hold live handles) while DBOS
  persists workflow inputs, so a workflow takes a **serializable scope key**
  and reconstructs `Deps` inside the run.
- A **DBOS spike on MiniMax-M3 + Friday's SQLite is a prerequisite** to the
  DBOS phase and is not yet done.
