# 05: Diagnose, say what was not checked, and escalate on three conditions

**What to build:** The diagnosis agent and node, its answer shape, its
memory tools, and the guarded step to the database.

**Blocked by:** 02, 03, 04. (Was also 07: knowledge rows enrich a
diagnosis, they do not gate building one — `Diagnose` with an empty
knowledge table must work, and is the baseline.)

**Decisions:** D8, D9, D12.

**Status:** ready-for-agent

**Revised 2026-09-17 (spec, v3.2):** `Diagnose` only reasons. Its tools are
`collect`, `memory_search`, `memory_add(finding)`, the skill tools and its
answer tool; it holds no fetching tool. There is no `db` or `conclude` node —
`InspectDatabase` is a check in `Gather`, by rule. The answer tool refuses a
`ref` no `Evidence` holds, and a supervisor in code scores the `Diagnosis`
and routes it (spec, same section).

## What

**Revised 2026-09-18 (spec, "Diagnose's context"):**
- `Evidence` splits into `claims[{claim, quote, ref}]` (in context) and
  `spill_path` (on disk); each check distils by rule, under the per-check line
  caps in the spec's table. `read_evidence(ref, radius)` is the just-in-time
  read.
- A dossier builder with a token budget (`diagnose.dossier_budget_tokens`)
  and the spec's priority order; a cut check leaves one line and a
  `not_checked` entry "cut for budget".
- Input order: instructions · knowledge · notes from the previous run ·
  dossier · question. A prefix-stability test, as triage has.
- `Diagnosis` gains `hypotheses[]`, `alternatives_rejected[]` (≥ 1 when
  `conclusive`), `distinguishing_check`.
- The code supervisor's objections return as the answer tool's output, one
  correction turn.
- Previous-run notes: the stored `Diagnosis` distilled to ≤ 500 tokens and
  injected on a re-run.


- `Harness(answers=Diagnosis)`: `cause`, `evidence` (verbatim lines),
  `code_path`, `suggested_fix`, `confidence`, `conclusive`, `not_checked`.
- Memory tools wired, writing `finding` scoped to the channel; the
  instruction-shape guard applies unchanged. The channel's dependency
  knowledge (ticket 07) is injected the way domain kinds already reach the
  extractor.
- Escalation predicate, in code: `not conclusive and no stack for this
  request and the dependency knowledge names a table and key`. Only then a
  read through `db-generic`. First fact to fetch: its tool list, and whether
  the account is read-only on both environments — the operator did not
  answer that, and the ticket must.
- A skill slot: the agent is told which skill to fetch for the report's
  shape, from `config.yaml`.

## Verify

- Tests for each of the eight combinations of the three conditions; only one
  escalates.
- The `Reply`-construction anchor test stays green: this node writes no
  `Reply`.

## Owed by the slice (ticket 00, 2026-09-20)

The slice built `Diagnose` as one model call over fixed evidence, no tools,
answering `Diagnosis{cause, confidence, conclusive, refs, next_checks}` with
**pointers, not quotes** — every line of the prompt carries an id, the model
names ids, code puts the text back, and a pointer that resolves to nothing
voids the answer. So does calling an answer conclusive while pointing at
nothing.

Still owed here:

- **`alternatives_rejected`**, and with it `hypotheses` and
  `distinguishing_check`. The spec's build order is "minimal prompt, the
  dossier with its budget, the shape with `alternatives_rejected`, the
  grounding gate"; the slice shipped the first and the last.
- **The dossier budget.** The slice uses fixed line caps
  (`friday/dag/api_issue/logs.py`), not `diagnose.dossier_budget_tokens` and
  the priority order, and no check is cut with a "cut for budget" line.
- **`not_checked` as `[{kind, reason}]`.** The slice's is a list of strings,
  written by code rather than by the model — the nodes already know what they
  skipped, and that half is worth keeping.
- **Moving the grounding gate into the answer tool**, so a bad pointer is
  corrected inside the model's own turn budget instead of voiding the run
  after it (D8).
- **The numbers ticket 00 asks for in its answer 3** — dossier tokens, wall
  time per node, model calls — are in `node_runs` and `model_calls` but not
  in the report file. Whichever of the two is the operator's reading surface
  should carry them.
