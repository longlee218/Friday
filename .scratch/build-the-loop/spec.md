# Build the loop — execution board

Executes the shape locked by the decision map
[The graph becomes a loop](../the-graph-becomes-a-loop/map.md). This board does
not re-decide anything; every ticket cites a decision already made there.

## Target shape

```
Intake → [Acknowledge] → Diagnose (one bounded agent loop) → Report
```

Replaces the 7-node `devops.api_issue` graph
(`Prepare → Resolve → Acknowledge → FindRequestLog → ReadFailingCode →
Diagnose → Report`). Scope: `api_issue` only.

## Ground rules

- **Suite green at every step** (`uv run pytest -q`, whole suite), and a
  `code-review` subagent per change — per `CLAUDE.md` § Verifying a change.
- **The grounding gate is inviolable**: Evidence line-ids (`Lnn`), a ref that
  resolves to nothing voids the answer. No step may weaken it.
- **Measure before you cut**: the eval foundation (ticket 01) lands before the
  baseline is deleted (ticket 08); a diagnose change re-runs
  `evals/run_api_issue_eval.py` and reports the numbers.

## Sequence (blocking in each ticket)

1. Superset capture + canned source — the eval foundation.
2. Diagnose loop output `Diagnosis | Ask | HandOver`.
3. The `Intake` node (deterministic).
4. Checkpoint/resume on `placement_identity`.
5. Remove `FindRequestLog` + `ReadFailingCode`.
6. Rewire the graph; drop extraction/`prepare` for api_issue.
7. Operator: capture ≥ 10 cases (gates 08).
8. Delete the fixed-feed baseline (`diagnose_reads=False`).
9. Docs debts: CONTEXT.md § Action, `placement identity` vocab, DESIGN.md, ADR.

## Doc debts carried from the decision map

- `CONTEXT.md` § Action: findability is **no longer a gate** (reverses the
  documented rule) — correct it, consider an ADR.
- `CONTEXT.md` § Vocabulary: add `placement identity`.
- `docs/DESIGN.md` § What exists / § Reasoning: the new shape.
