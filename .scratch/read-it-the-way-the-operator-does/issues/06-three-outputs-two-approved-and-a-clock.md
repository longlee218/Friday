# 06: Three outputs, two of them approved, and a clock

**What to build:** The graph's edges end to end, its outbound rows, the
report file, and the timeouts.

**Blocked by:** 01, 02, 03, 04, 05.

**Decisions:** D10, D11, D13.

**Status:** ready-for-agent

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
