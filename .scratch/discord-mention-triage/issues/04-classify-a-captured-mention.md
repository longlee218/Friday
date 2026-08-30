# 04: Classify a captured mention

**What to build:** A captured mention is understood — it either becomes a task with a
category, or is recorded as noise and goes no further. Carries the reasoning runtime
that every later step reuses: the node record type, the model loop, its limits, and
per-node checkpointing.

**Blocked by:** 01

**Status:** ready-for-agent

- [ ] A message describing a defect produces a task carrying its category and a confidence score
- [ ] Small talk is recorded as a classified event with no task created
- [ ] Classification reads the surrounding conversation, so a follow-up referring to an earlier message is understood in context
- [ ] A follow-up in a conversation that already has an open task updates that task rather than opening a second one
- [ ] A follow-up whose category differs from the open task's is escalated for human input rather than silently relabelled
- [ ] A classification below the configured confidence threshold is escalated for human input
- [ ] A model error, timeout, or unparseable response is retried, then escalated for human input — never discarded
- [ ] A model refusal is escalated for human input rather than raised as an error
- [ ] Exceeding the configured turn limit or token limit escalates for human input
- [ ] Every classification decision is recorded with its score, so the threshold can later be derived from real data
- [ ] Each node run persists its structured result plus a trimmed record of its tool activity
