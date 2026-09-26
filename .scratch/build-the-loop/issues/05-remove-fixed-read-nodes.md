Status: ready-for-agent
Blocked by: 03, 07

# Remove FindRequestLog + ReadFailingCode as fixed nodes

Decision: charting (the reads become the loop's tools; the loop already has
`read_log` / `read_code` / `what_code_means`).

## Re-gated on ticket 7 (2026-09-26)

`diagnose_reads` defaults to **False** (`plugins/devops/config.py`), so the live
path is **dossier-mode**, and dossier-mode reads `state["find_request_log"]` +
`state["read_failing_code"]` directly (`diagnose.py`). Those two nodes are the
**fixed-feed baseline**. Removing them here = deleting that baseline — which
[ticket 8](08-delete-fixed-feed-baseline.md) gates behind **ticket 7**
(≥10 captured cases + eval showing agentic ≥ baseline: the measure-before-cut
discipline). So this ticket now waits on ticket 7 too (`Blocked by: 03, 07`, was
`03`). This ticket (remove the two nodes + the dossier diagnose path) and ticket
8 (remove the `diagnose_reads` flag) are the two halves of cutting the baseline;
neither runs before the measurement in ticket 7.

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

- [ ] Graph no longer contains the two nodes; the loop covers their reads.
- [ ] No orphaned imports/functions left by this change.
- [ ] Whole suite green; `code-review` done; `run_api_issue_eval` numbers
      reported.
