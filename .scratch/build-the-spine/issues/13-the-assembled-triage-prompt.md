Status: done
Blocked by: 05

# The assembled triage prompt

Decision: [The recognition reasoning and the assembled triage prompt](../../domains-plug-in/issues/02-the-recognition-reasoning-and-the-assembled-triage-prompt.md).
Stub: `recognition_and_triage_prompt_STUB.py` on its prototype branch.

## Goal

- Each of the three actions declares its `Action` with `Recognition`
  (`means`, `pick_when`, `not_when`, `examples`) — text from the stub, and
  for `answer_question` from ticket 06 §9.
- Triage prompt = core reasoning (the old ladder minus its order; "no label
  comes first") + `## The labels` rendered sorted by name, `skip` last +
  examples adding up (declared → core `skip` → DB-confirmed).
- Answer schema: `type` a `Literal` closed to registered actions + `skip`.
- `config.yaml` `triage_examples`, `_type_doc` / `_means` go.
- Suite test: no declared example also in `evals/triage.jsonl`.
- Ticket 06's boundary check: the three `trace_problem` eval rows that read
  like "how does it work" are checked against 06 §9 — operator confirms each
  relabel.

## Acceptance

- [ ] `run_triage_eval` run: accuracy, confusion matrix, threshold table
      reported, compared with the 2026-09-20 100%. — **Not done**: run
      2026-09-29 on this change and on HEAD `330e06c` as a baseline; every
      call answered HTTP 402 (OpenRouter account has no credits). Deferred
      until credits exist.
- [x] Rendered prompt bytes stable across runs (test).
- [x] Whole suite green; `code-review` done.

## Decided while building (operator, 2026-09-29)

- The three `Action`s register with real contracts from the decisions, and
  the agents they grant (`backend.diagnose`, `backend.explain`) register as
  `AgentSpec`s so boot refusal 4 passes; `backend.explain` is on `flash`
  (config declares no `strong`). Nothing runs them until 14/15.
- Boundary check (06 §9): eval rows #23, #32 and #34 stay
  `backend.trace_problem` — each reports behaviour that happened.
- Triage runs on the new `free` tier (operator's change).
