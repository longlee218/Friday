Status: done
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

## Scope revised 2026-09-26 (during /implement)

Two decisions taken before building (see the map's Decisions-so-far):
- **Mechanism = terminal output tools**, not a Harness `answers=` union: the
  Harness forces a single answer shape, so a run-ending outcome is a second
  *output tool* the model calls. Added `Harness(ends_with=[...])`.
- **Ask split to ticket 4.** `Ask` (needs the reporter) is a pause/resume, which
  is ticket 4's job; building it here would build it twice. So ticket 2 lands
  **`HandOver` + the outcome-mapping seam**; `Diagnosis` is unchanged; `Ask`
  arrives with ticket 4's resume.

## Acceptance

- [x] A solved run emits `Diagnosis` (unchanged); a run the model cannot handle
      emits `HandOver` via the `hand_over(reason)` terminal tool.
      **`Ask` deferred to ticket 4** (recorded decision above).
- [x] `HandOver` maps to the existing `Action=HandOver`; `Diagnosis` still flows
      to `Report` (a `Reply`). Verified:
      `test_the_reading_loop_can_hand_over_instead_of_answering`, and the
      Harness seam in `test_a_terminal_tool_finishes_the_run_beside_the_answer`.
- [x] Grounding gate still voids a `Diagnosis` whose ref resolves to nothing —
      `_judged`/`unresolved_refs` unchanged; covered by the existing
      `..._pointing_at_a_line_it_was_not_shown_voids_it` tests.
- [x] Whole suite green; `code-review` done — suite green (**1620 passed, 1
      skipped**, with `OPENROUTER_API_KEY` set). `code-review` ran and found 4
      issues, all addressed:
      - **#1 SEVERE (feature inert):** the ungated `diagnose→report` edge let
        the walk fall through to `report`, which produced its own generic
        hand-over and discarded the model's reason. Fixed: gated the edge with
        `_did_not_decide("diagnose")`; end-to-end guard test verified red
        without the gate. (Also closes **#4** — the isolation-only test gap.)
      - **#2 latent:** a terminal tool whose return type won't resolve would be
        callable but its output silently lost. Fixed: raise at build; test.
      - **#3 low:** terminal-tool name collisions. Fixed: build-time guard; test.

## Result

`HandOver` reaches the operator end-to-end (edge stops the walk at diagnose, the
pool reads the Action off the state) carrying the model's own `reason`.
`Diagnosis` path and the grounding gate are unchanged. `Ask` deferred to ticket
4 (recorded decision). Harness `ends_with` seam is general (guarded: needs
`answers`, resolvable return type, no name collision) and its class-level
default fixed a real regression in the extraction test doubles.
