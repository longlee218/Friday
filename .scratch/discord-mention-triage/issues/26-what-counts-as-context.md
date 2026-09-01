# 26: Only what concerns the operator, and only the parts that matter

**What to build:** A model is shown the part of a conversation that actually involves
the operator, opened with the messages the work began from and followed by what has
been said since — rather than the last twenty messages in the channel, whoever they
were between.

**Blocked by:** None (can start immediately)

**Status:** done

Two problems, and they are separate.

**Relevance.** Today a conversation that once mentioned the operator keeps *every*
message in it as context. In a quiet channel that is fine; in a busy one it is
twenty messages of other people's business, and context that is wrong misleads a
classification far more expensively than a few hundred tokens cost.

What counts is structural, not inferred: the message mentions them, they wrote it, or
it replies to something they wrote. The third needs something not currently kept —
inbound messages record what they reply to only for messages we send, not for
messages we receive.

Filtering happens when context is *assembled*, not when a message is stored. This
definition will change, and a message thrown away cannot be reconsidered under a
better one.

**Shape.** A sliding window of the last N messages changes on every call, which means
nothing before it can be cached. An anchored opening plus what has arrived since is
stable at the front and short at the back — the same thing the reporter would tell
you, and the shape a cache can hold.

- [x] An inbound message records what it replies to
- [x] Context contains only messages that mention the operator, were written by them, or reply to them
- [x] Changing that definition changes what is shown without needing history that was never stored
- [x] A task's context opens with the messages it began from, and those do not change as the conversation continues
- [x] Messages that arrive after a task opens are appended rather than displacing the opening
- [x] The proportion of a prompt that is identical between two consecutive calls is measurable, so caching can be confirmed rather than assumed

## Done

`InboundEvent` and the `messages` table now carry `reply_to`, populated in
`friday/providers/discord/normalise.py` from Discord's own reply reference.
`Database.relevant_messages(conversation)` reads (never writes) the structural
filter: mentions the operator, was written by them, or replies to a message of
theirs — an `is_own` id lookup makes the third signal a plain `IN` query rather
than a join.

The anchored-prefix-plus-append shape turned out not to need its own state.
Filtering already keeps a busy channel's unrelated traffic out, so dropping the
sliding window's `limit` entirely — unbounded, oldest first — gives the same
guarantee for free: a relevant message, once included, is never evicted by a
later one arriving, so the prefix a model saw on call N is still there
untouched on call N+1. No `anchor_message_id` column, no per-task boundary to
maintain. `TriageRunner` and `WorkflowRunner._say` both read through
`relevant_messages` now instead of `db.messages(..., limit=N)`; the sliding
window's `context_messages` config still exists but now only governs how much
history the inbox *seeds* when a conversation first mentions us — a write-time
concern this ticket deliberately left alone.

`tests/test_context_relevance.py::test_the_shared_prefix_between_two_calls_is_almost_the_whole_prompt`
is the measurability criterion: it builds the same conversation's prompt before
and after 24 lines of unrelated noise arrive and asserts the shared prefix is
over 90% of it — a sliding window would have shared almost none.

Left alone, per the ticket: per-channel YAML context files, the base/override/
derived split, and rebuild scheduling — that's ticket 25, built in parallel.
