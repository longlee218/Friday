Status: done
Blocked by: 06

# Doc debts: CONTEXT.md § Action, vocabulary, DESIGN.md, ADR

Decisions: charting Q2 (findability not a gate — reverses a documented rule),
ticket 01 (new term `placement identity`).

## Goal

Land the documentation the shape change owes — ideally in the same commits as
the code, gathered here so none is forgotten.

- `CONTEXT.md` § Action: rewrite the findability rule — a correlationId/curl is
  **no longer a precondition gate**; the loop investigates and `Ask`s only when
  stuck. This reverses the current text ("a precondition belongs in the gate").
- Decide whether this reversal warrants an **ADR** (hard to reverse + surprising
  + a real trade-off → likely yes); if so, write it under `docs/adr/`.
- `CONTEXT.md` § Vocabulary: add **`placement identity`** (the `(env, service,
  clone, repo, tag)` staleness key).
- `docs/DESIGN.md` § What exists / § Reasoning: the `Intake → Diagnose loop →
  Report` shape; retire the 7-node description.

## Acceptance

- [x] CONTEXT.md § Action no longer states findability as a gate. (Rewritten:
      the precondition rule holds for single-node types; `api_issue` is no longer
      gated — the loop reads log/code/docs and `ask_reporter`s only when stuck.)
- [x] `placement identity` in § Vocabulary. (New `## Placement identity` entry:
      the `(env, service, clone, repo, tag)` staleness key.)
- [x] DESIGN.md reflects the new shape; ADR written. (§ What exists row, §
      Reasoning shape + the v3.3 "decided, not built" → built, § Workflows
      diagram, and the extractor "ask for what is missing" note all updated to
      `intake → acknowledge → diagnose loop → report`; old text kept as
      superseded history. **ADR 0002** written — findability is not a
      precondition for `api_issue`.)
- [x] Whole suite green. (`uv run pytest -q`: 1575 passed, 1 skipped, 7
      pre-existing `OPENROUTER_API_KEY` env failures unrelated.)
