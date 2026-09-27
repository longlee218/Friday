---
Status: accepted (operator, 2026-09-27)
---

# Findability is not a precondition for an `api_issue` investigation

## Context

The original `api_issue` design gated the investigation on **findability**: a
report needed a `curl` **or** an `endpoint`+`identifier` pair (later "a
correlationId or a curl") before the graph would investigate; a report without
one took an *ask-for-details* path instead. The rule was stated as a principle —
*"a precondition belongs in the gate, not in the last node's else branch"* — and
lived in `CONTEXT.md` § Action and in the extractor's per-type required-ness.

Board `build-the-loop` rewired `api_issue` from the fixed pipeline
`Prepare → Resolve → FindRequestLog → ReadFailingCode → Diagnose → Report` to an
agentic loop `Intake → Acknowledge → Diagnose (loop) → Report`. Two facts made
the old gate wrong under the new shape:

1. **`Intake` is deterministic and never asks.** It replaced the extractor; it
   makes no model call and always produces an `IntakeContext`, so the upfront
   "ask for the missing curl" no longer has a node to live in.
2. **The loop reads code and docs, not only logs.** A case with no matching log
   environment (`env == "external"`) — or with no correlationId/curl — is still
   investigable from the repository and its docs. The old gate assumed the only
   evidence was a log line found by a request id, which is no longer true.

## Decision

**A `curl`/`correlationId`/endpoint is no longer a precondition to open an
`api_issue` investigation.** `Intake` gathers what it can deterministically; the
`Diagnose` loop investigates with its tools (`read_log`, `read_code`,
`what_code_means`) and calls **`ask_reporter`** for a missing piece only when it
is genuinely stuck. The reporter is still **acknowledged** immediately (an
unapproved "being looked at"), and the final answer is still a `Reply` that waits
for approval.

This **reverses** the documented "a precondition belongs in the gate" rule *for
`api_issue`*. It still holds for single-node types (e.g. `access_request`), whose
whole graph is a node-0 extract-then-ask.

## Considered options

- **Keep the findability gate (ask upfront when no curl/id).** Rejected: it
  refuses cases the loop could now solve from code/docs, and it has no node to
  run in after the extractor was removed. It optimises for "fail fast" at the
  cost of never trying.
- **Gate `Acknowledge` on a resolved service / non-external env** (so
  unhandleable cases are not acknowledged). Rejected by the operator: the input
  is not always a curl, and `external` is not "unhandleable" — the loop reads
  code and docs. Acknowledging always, with generic wording when the service is
  vague, was chosen instead.

## Consequences

- `CONTEXT.md` § Action and `docs/DESIGN.md` (§ Reasoning, § Workflows, and the
  extractor "ask for what is missing" note) are updated; the old gate text is
  kept as superseded history.
- More cases enter the loop, including `external` ones, so the loop must be able
  to hand over cleanly (`hand_over`) and to ask (`ask_reporter`) — both are
  built.
- The trade accepted: immediate reassurance and a genuine attempt on every
  report, versus the old behaviour of turning some reports away at the door. A
  case Friday ultimately cannot handle now receives an acknowledgement before it
  is handed to a human.
- Whether the loop diagnoses at least as well as the old fixed-feed baseline is
  **not yet measured** — Friday has never run against real traffic, so the
  `run_api_issue_eval` gate is deferred to post-launch calibration (board
  `build-the-loop` tickets 7/8, option B).
