Status: ready-for-agent
Blocked by: 03

# Remove FindRequestLog + ReadFailingCode as fixed nodes

Decision: charting (the reads become the loop's tools; the loop already has
`read_log` / `read_code` / `what_code_means`).

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
