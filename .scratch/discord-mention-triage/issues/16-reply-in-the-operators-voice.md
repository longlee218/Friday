# 16: Reply in the operator's voice

**What to build:** A task that has been worked out produces a written reply that
sounds like the operator, shown to them for approval before anyone else sees it.
This is the second agent, and the first thing that speaks to other people in their
name.

**Blocked by:** 12, 06. Unblocks 14 — the responder is the second agent, which is
what turns the harness from a hypothetical seam into a real one.

**Status:** ready-for-agent

Tone comes from **few-shot examples of the operator's real past replies**, not a
written style guide: real examples carry a voice that description does not. This is
why their own messages are retained as context while still being skipped as
triggers — a stored conversation missing one side of itself teaches nothing.

The hard part is not the writing, it is **staleness**. A draft is written against a
conversation that keeps moving, and approval happens minutes or hours later. Posting
an answer to a question that has since been withdrawn, corrected, or already
answered by someone else is worse than posting nothing.

- [ ] A reply is drafted for a task that is ready for one, in the operator's voice, learned from their own past messages rather than from a description of their style
- [ ] Nothing reaches a channel without approval
- [ ] A draft records the state of the conversation it was written against
- [ ] Approving a draft that newer messages have overtaken does not post it — the task returns for rework instead, so posting a stale answer is impossible rather than unlikely
- [ ] A burst of follow-up messages does not produce a draft per message
- [ ] Direct posting without approval can be enabled per task type on evidence, without reworking the flow — the request for missing details is the first candidate, once a run of them has been approved unchanged
