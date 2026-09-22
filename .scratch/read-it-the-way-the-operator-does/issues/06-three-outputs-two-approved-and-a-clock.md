# 06: Three outputs, two of them approved, and a clock

**What to build:** The graph's edges end to end, its outbound rows, the
report file, and the timeouts.

**Blocked by:** 05's remainder for the `[db]` step only (2026-09-22).
01, 02, 03 and 04 no longer gate this.

**Decisions:** D10, D11, D13.

**Status:** part done (2026-09-22). The graph runs end to end —
`prepare → resolve → find_request_log → read_failing_code → diagnose →
report` — with `resolve` ending the run on an external domain, and `report`
writing `data/reports/<task_id>.md` (D10) and handing over. Run against a
real production case; no seam broke. A run where the model provider was
unreachable degraded correctly: an `error` envelope naming the reason, the
report still written, nothing pretending to have concluded.

**What is left, all of it the outbound half:**

- The "đang xử lý" acknowledgement, queued awaiting approval. Nothing is
  queued today; `report` hands over and that is the end.
- The operator's DM with the summary and the path, and the reporter's brief
  queued awaiting approval. Neither exists — no outbox row is written by
  this graph at all, which `friday/dag/api_issue/report.py`'s own docstring
  already says is owed to this ticket.
- The `[db]` step (ticket 05).
- `dag.api_issue.timeout_seconds` and per-node ceilings in `config.yaml`.
  Nodes take a `timeout_seconds` argument, and the file sets none; the only
  `timeout_seconds` in `config.yaml` are the agents'.


**How to check any of this rather than believe it.** The run writes its
report into a throwaway directory, so `data/reports/` is empty and nothing
in the tree records that a run happened. What *is* on disk is the case
itself, and it is enough to reproduce every claim here:

    uv run replay_case.py --case data/cases/prod-onboarding-400.json --diagnose

`data/cases/prod-onboarding-400.json` also corroborates the log
measurements directly, without running anything: its `reads.window` is 400
lines with `truncated: true` spanning 10:38:20–10:39:44Z — 84 seconds of
the 35 minutes asked for — and its `reads.narrowed` is 2 lines with
`truncated: false`. The file is under `data/`, which is gitignored on
purpose: those lines carry `userId`, `ip` and `deviceId`.

## What

- Node order: prepare → route (external ends here) → "đang xử lý" queued,
  awaiting approval → logs (prod | dev) → code → diagnose → [db] → finish.
- Finish writes `data/reports/<task_id>.md`, DMs the operator the summary
  and the path, and queues the reporter's brief, awaiting approval. The
  brief is the second place a `Reply` is built or it is the same place;
  the anchor test decides, and the ticket says which.
- `scrub` on both outbound rows and on the board's rendering of the report;
  not on the prompt or the file.
- `config.yaml`: `dag.api_issue.timeout_seconds` (default 300) and per-node
  ceilings; a node that times out passes what it has to finish, and
  `not_checked` names it.

## Verify

- An end-to-end scripted run for each of: stack found, business error with
  escalation, not found then answered, external domain.
- Checkpointed resume after the not-found ask.
