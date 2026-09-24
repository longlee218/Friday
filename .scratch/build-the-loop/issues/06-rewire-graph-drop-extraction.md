Status: ready-for-agent
Blocked by: 03, 04, 05

# Rewire the graph; drop extraction/prepare for api_issue

Decisions: charting Q1 (drop extraction), the target shape.

## Goal

Assemble the final shape and remove node 0 for `api_issue`:

```
Intake → [Acknowledge] → Diagnose (loop) → Report
```

- Replace `prepare`/extraction with `Intake` as node 0 (the fresh-each-pass
  anchor). **Only for `api_issue`** — the shared `prepare_node` and other task
  types are untouched.
- Keep `Acknowledge` as a standalone unapproved send; confirm its trigger/text
  under the new shape (fog item — decide here).
- Confirm `Report` is unchanged (still a `Reply` that waits approval) or record
  the minimal change.

## Acceptance

- [ ] `api_issue` runs the four-stage shape end to end (replay a case).
- [ ] No extractor/`prepare` on the `api_issue` path; other task types
      unchanged.
- [ ] `Acknowledge` still precedes the slow part, no approval.
- [ ] Whole suite green; `code-review` done; `run_api_issue_eval` reported.
