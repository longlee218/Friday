# 26: Only what concerns the operator, and only the parts that matter

**What to build:** A model is shown the part of a conversation that actually involves
the operator, opened with the messages the work began from and followed by what has
been said since — rather than the last twenty messages in the channel, whoever they
were between.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

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

- [ ] An inbound message records what it replies to
- [ ] Context contains only messages that mention the operator, were written by them, or reply to them
- [ ] Changing that definition changes what is shown without needing history that was never stored
- [ ] A task's context opens with the messages it began from, and those do not change as the conversation continues
- [ ] Messages that arrive after a task opens are appended rather than displacing the opening
- [ ] The proportion of a prompt that is identical between two consecutive calls is measurable, so caching can be confirmed rather than assumed
