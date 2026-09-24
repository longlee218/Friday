Status: ready-for-agent
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

- [ ] CONTEXT.md § Action no longer states findability as a gate.
- [ ] `placement identity` in § Vocabulary.
- [ ] DESIGN.md reflects the new shape; ADR written or explicitly declined with
      a reason.
- [ ] Whole suite green.
