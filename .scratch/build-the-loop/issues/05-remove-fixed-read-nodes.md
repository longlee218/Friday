Status: done
Blocked by: 03

# Remove FindRequestLog + ReadFailingCode as fixed nodes

Decision: charting (the reads become the loop's tools; the loop already has
`read_log` / `read_code` / `what_code_means`).

## Option B — un-gated (2026-09-26)

Was briefly `Blocked by: 03, 07` (measure before cutting the baseline). The
operator confirmed Friday has **never launched**, so no real cases can be
captured and no measurement is possible. Under option B ([[build-the-loop-scoring-deferred]],
see the map / STATUS) the "measure before cutting" discipline is deferred to
*post-launch calibration* — there is no running baseline to protect pre-launch.
So this is un-gated back to `Blocked by: 03`.

**Couples with ticket 8.** Removing the two nodes removes the dossier feed, so
the dossier diagnose path goes too, so the reads loop becomes the *only* diagnose
mode, so the `diagnose_reads` flag is vestigial. To leave no orphan, this change
also removes the flag — ticket 8's code half. Ticket 8's remaining concern (the
eval showing agentic ≥ baseline) stays parked with ticket 7.

## Goal

Delete the two fixed pre-fetch nodes from the `api_issue` graph; the Diagnose
loop reads for itself.

- Remove `find_request_log_node` + `read_failing_code_node` from the graph and
  their edges.
- Remove any now-orphaned code that only those nodes used (only what this change
  orphans — not pre-existing dead code).
- The `not_checked` list the fixed nodes authored now comes from the loop's
  Evidence / tool answers.

## Acceptance

- [x] Graph no longer contains the two nodes; the loop covers their reads.
      (Shape verified live: `prepare → resolve → acknowledge → diagnose → report`;
      `graph/code.py` deleted, `find_request_log`/`read_failing_code` gone from
      `logs.py`/`__init__.py`; reads-mode `_reading` is the only diagnose path.)
- [x] No orphaned imports/functions left by this change. (code-review confirmed
      no surviving importer of any removed symbol; the dossier-only helpers,
      `build_input`/`numbered`, and the `diagnose_reads` flag are gone; `_reported_at`,
      `build_instructions`/`build_reads_input` kept. Three pre-existing stale
      *references* outside this ticket's files left as follow-ups — see below.)
- [x] Whole suite green; `code-review` done. `run_api_issue_eval` **not run**
      (option B: no model key and no captured cases — parked with ticket 7).
      (`uv run pytest -q`: 1580 passed, 1 skipped, 7 pre-existing
      `OPENROUTER_API_KEY` env failures unrelated. code-review: clean deletion,
      **no `_judged` gate lost coverage** — the seven gates were rewritten
      through reads-mode with gate-specific reason assertions + a real `read_log`
      call anchor; one guard deleted-and-watched-red.)

## Follow-ups (stale references outside this ticket's scope, harmless)

- `replay_case.py` (dev CLI) still `.get("find_request_log"/"read_failing_code", {})`
  — degrades to empty, no error. Tidy in a later replay-CLI pass.
- `friday/sdk/sources.py` `CodeSource` docstring mentions "the `read_failing_code`
  check" — doc drift; core SDK, out of scope here.
- `tests/test_api_issue.py::test_the_report_is_written...` still seeds now-unread
  `find_request_log`/`read_failing_code` state — cosmetic, `render` ignores it.

## Ticket 8 relationship

The `diagnose_reads` flag + dossier baseline path were removed here (ticket 8's
code half), because leaving them orphaned this change. Ticket 8's remaining
concern — the eval showing agentic ≥ baseline — needs real data and stays parked
with ticket 7 (option B).
