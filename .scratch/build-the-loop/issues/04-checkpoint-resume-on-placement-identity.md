Status: wontfix
Blocked by: 06

# Checkpoint / resume on placement identity

> **Superseded 2026-09-28** by [The durable spine workflow and pause/resume](../../domains-plug-in/issues/14-the-durable-spine-workflow-and-pause-resume.md):
> resume becomes the spine's continuation point (a stored `Ask` carries the
> agent's history + `Evidence`) and `placement_identity` joins `step_key`.
> The acceptance below moves to the spine's build board.

Decision: [The reply that both resumes and invalidates](../../the-graph-becomes-a-loop/issues/01-the-reply-that-both-resumes-and-invalidates.md).

## Split (2026-09-26)

The **`Ask` outcome** (`ask_reporter` terminal tool) was split out to
[ticket 10](10-ask-reporter-terminal-tool.md) and **shipped** — it is buildable
on the ticket-2 seam without any resume machinery. What remains here is the
**resume half**, and it now depends on **ticket 6**: it makes `Intake` the
staleness anchor and resumes the diagnose loop, neither of which is on the live
graph until ticket 6 rewires it. Building the resume before then would be inert.
Hence `Blocked by: 06` (was `02, 03`).

## Goal

Make `Intake` the staleness anchor and let an `Ask` resume without re-burning
reads:

- `placement_identity` becomes the checkpoint **discard key** (replaces node-0
  output); it runs fresh each pass, never checkpointed.
- Store the Diagnose agent's `message_history` + `Evidence` in its checkpoint.
- On an incoming reporter message: re-run `Intake`, diff `placement_identity` —
  **unchanged** → checkpoint survives, resume by appending the reply as a
  reporter turn to `message_history`; **changed** → discard + re-investigate.
- No reply-intent classifier, no separate change-detector. The mixed case falls
  out of the rule.

## Acceptance

- [ ] Answering an `Ask` with unchanged placement resumes the loop; `Lnn` ids
      stay stable (grounding intact); reads are not repeated.
- [ ] A reply that flips env/service discards and re-investigates.
- [ ] Whole suite green; `code-review` done.
