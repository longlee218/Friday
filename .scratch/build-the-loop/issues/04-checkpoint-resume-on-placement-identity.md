Status: ready-for-agent
Blocked by: 02, 03

# Checkpoint / resume on placement identity

Decision: [The reply that both resumes and invalidates](../../the-graph-becomes-a-loop/issues/01-the-reply-that-both-resumes-and-invalidates.md).

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
