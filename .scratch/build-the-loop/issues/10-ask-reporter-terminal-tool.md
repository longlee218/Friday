Status: done
Blocked by:

# ask_reporter terminal tool (the Ask outcome)

Split from ticket 04 (2026-09-26): the `Ask` outcome of the diagnose loop is a
terminal output tool, buildable now on the ticket-2 seam. The **resume** half of
ticket 04 (placement_identity checkpoint) stays in ticket 04, deferred to after
ticket 6 (it resumes a loop not yet on the live graph).

Decision: [What Intake gathers, and the shape the loop returns](../../the-graph-becomes-a-loop/issues/03-what-intake-gathers-and-the-shape-the-loop-returns.md)
(loop output `Diagnosis | Ask | HandOver`).

## Goal

Add `ask_reporter(question) -> Ask` as a terminal output tool on the diagnose
reads loop, mirroring ticket 2's `hand_over(reason) -> HandOver`:

- The model calls it when a missing piece only the reporter has (a correlationId,
  the failing request, the environment) blocks the diagnosis; the loop ends and
  the node returns the `Action=Ask`.
- Wire it into the factory's `ends_with=[hand_over, ask_reporter]`.
- The `diagnose -> report` edge must not fire on an `Ask` (already handled:
  `_did_not_decide` excludes `Ask`).

## Acceptance

- [x] `ask_reporter(question)` returns `Ask(text=question)`
      (`test_ask_reporter_returns_an_ask_action`).
- [x] The reads loop returns the `Ask` instead of a diagnosis, before the
      "answered without reading" gate — an `Ask` without reads is not turned into
      an empty envelope (`test_the_reading_loop_can_ask_the_reporter_instead_of_answering`).
- [x] An `Ask` from the loop ends the walk, never reaches `report`
      (`test_an_ask_from_diagnose_ends_the_walk_instead_of_reaching_report`).
- [x] Whole suite green; `code-review` done. (`uv run pytest -q`: 1627 passed,
      1 skipped, 7 pre-existing `OPENROUTER_API_KEY` env failures unrelated.
      code-review: clean mirror of `hand_over`, no critical/high/medium; the
      `(Ask, HandOver)` guard deleted-and-watched-red.)
