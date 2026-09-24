Status: ready-for-agent
Blocked by:

# Diagnose loop output: Diagnosis | Ask | HandOver

Decisions: [What Intake gathers, and the shape the loop returns](../../the-graph-becomes-a-loop/issues/03-what-intake-gathers-and-the-shape-the-loop-returns.md),
[The reply that both resumes and invalidates](../../the-graph-becomes-a-loop/issues/01-the-reply-that-both-resumes-and-invalidates.md).
Stub: `../the-graph-becomes-a-loop/intake_and_loop_output_STUB.py`.

## Goal

Extend the `diagnose_reads` agent's `output_type` to the union
`Diagnosis | Ask | HandOver`, mapped onto Friday's existing Actions:

- `Diagnosis` (unchanged) → `Report` (a `Reply`, waits approval).
- `Ask(question, missing)` → `Action=Ask` (reporter); loop ends at the boundary.
- `HandOver(reason, found_so_far)` → `Action=HandOver` (operator).

`Ask`/`HandOver` are **loop-boundary exits** (Pydantic AI ends the run at the
boundary; no new machinery). `Ask` only after a genuine attempt — findability is
not an upfront gate.

## Acceptance

- [ ] A run that cannot proceed emits `Ask`; one that hits a risky/over-budget
      wall emits `HandOver`; a solved run emits `Diagnosis`.
- [ ] Each maps to the correct existing `Action`.
- [ ] Grounding gate still voids a `Diagnosis` whose ref resolves to nothing.
- [ ] Whole suite green; `code-review` done.
