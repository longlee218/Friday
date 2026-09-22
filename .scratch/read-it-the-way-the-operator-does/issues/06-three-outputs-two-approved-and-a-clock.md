# 06: Three outputs, two of them approved, and a clock

**What to build:** The graph's edges end to end, its outbound rows, the
report file, and the timeouts.

**Blocked by:** 05's `[db]` step, which is all that is left of this
(2026-09-22).

**Decisions:** D10, D11, D13.

**Status:** done except the `[db]` step, which is ticket 05's (2026-09-22).
Three outputs: the acknowledgement, sent unapproved on the operator's call;
the finding, direct-messaged to them with the cause and the report's path;
and the reporter's copy, which waits for approval as a `Reply`. The clock is
`api_issue.timeout_seconds`, checked at boot against the sum of the graph's
node ceilings — every node has one now, which three did not.

Its own title is out of date: **one** output is approved, not two.

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

## Re-scoped by architecture v3.3 (2026-09-22)

The operator's call: `Gather` gathers **metadata**, and `Diagnose` reads for
itself through tools. The three outputs are unaffected. The node order loses
`find_request_log` and `read_failing_code` once ticket 15's step 5 says it is
safe to remove them.

Nothing here is thrown away — the reading, the cutting and the guards are
what the tool is made of. What changes is **who decides what to look for**,
and nothing about what is called. See the spec's "Architecture v3.3" and
ticket 15.
