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

- [x] `run_triage_eval` run: accuracy, confusion matrix, threshold table
      reported, compared with the 2026-09-20 100%. — **Done 2026-09-29**, as
      `uv run run_eval.py core.triage` (the eval moved onto Pydantic Evals the
      same day), on the assembled prompt as refined afterwards (senior-backend
      thinking, 06 §9 amended): deepseek-v4.1-flash **34/35 (97.1%)**, 0 out of
      set, 2/35 below 0.7; the one miss (020) is held by the sensitive-word
      prefilter and never reaches the model. `backend.trace_problem` subset
      (23): deepseek 22/23, qwen3-30b 21/23, gpt-5-mini 21/23 — after the
      harness answer-tool schema fix (`cf93f34`); before it, qwen and
      gpt-5-mini answered `{}`. Below 2026-09-20's 100%, on a different and
      larger set (35 real-traffic cases, long turns and attachments).
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
