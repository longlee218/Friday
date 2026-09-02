# 03: prepare is node 0, and the checkpoint has two tiers

**What to build:** Reading everything the reporter has said and checking it
becomes the first node of the graph rather than a gate in front of it, and the
graph's saved work is keyed on what that node produced. A "cảm ơn anh" no
longer costs an investigation; a correlationId that arrives late does.

**Blocked by:** 01, 02

**Decisions:** D2, D7, D8

**Status:** ready-for-agent

## Why

The gate in front of the graph and the graph itself are the same mechanism
under two names, and keeping them apart costs the one thing the graph exists
to buy. The fingerprint today is taken over the task's stored parameters
*before* extraction has run on the new message, so the state is judged against
inputs that are one message out of date. Hashing the reporter's text instead
would swing the other way: any new sentence, thanks included, discards a
finished investigation.

Node 0's *output* is the honest key. It is computed from everything said so
far, and it changes exactly when what the graph knows changes.

## What the two tiers mean

| Reporter sends | node 0 | nodes 1+ |
|---|---|---|
| "API lỗi" | runs; no id → ask | never reached |
| "cảm ơn anh" | runs (new text); params unchanged | state kept, nothing re-runs |
| "cid là abc…" | runs; params changed | state discarded, investigation re-runs |

Node 0 is never checkpointed — there is new text, so it must be read. Nodes 1+
are checkpointed against its output.

## Acceptance criteria

- [ ] The graph's entry node extracts from everything the reporter has said
      and validates the result; nothing outside the graph does either
- [ ] Node 0 runs on every pass, and is never written to the stored state
- [ ] The stored fingerprint is a hash of node 0's output; nodes 1+ are
      discarded exactly when it changes and kept when it does not
- [ ] The three-message table above holds end to end, driven at the pool's
      `run_once` seam
- [ ] Parameters filled in by node 0 are still written back to the task, so
      the board shows what the system acted on
- [ ] Extraction still runs before validation, so a hallucinated field is
      caught rather than believed
- [ ] Exempt from the byte-identity rule — D2 and D7 change behaviour — and
      the ticket says so where a reader would otherwise look for a capture
- [ ] Each guard is removed in turn and watched go red, per tickets 34–41
