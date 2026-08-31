# 25: What the agent knows about this channel

**What to build:** The operator can tell the agent things it cannot learn — that this
channel is project A, that its logs live in namespace B — and the agent can add what
it has learned, without either overwriting the other.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

Two kinds of knowledge share one file, and the split has to be visible in it. The
**derived** part is written by the machine from what it has learned and is safe to
delete: it rebuilds. The **overrides** part is the operator's, and nothing may write
to it — that is what makes a correction stick, rather than surviving until the next
rebuild ten seconds later.

A file per channel, inheriting from a base file that holds what is true everywhere —
who the agent is, and how it behaves. YAML rather than JSON, for comments: a fact
without its reason is a fact nobody dares change.

Rebuilding is triggered by learning something, not by a clock. The promotion pass
already knows whether anything changed and already runs on a timer, so a rebuild
happens when there is something to rebuild and not otherwise.

The derived part carries what has been learned, and a summary of the conversation —
but the summary costs a model call, so it is written only when the conversation has
grown past a configured share of the model's context window. Below that, the raw
messages are cheaper than summarising them.

- [ ] The operator can create a channel's file and fill it in before the agent has learned anything
- [ ] What the operator writes is never overwritten by a rebuild, and the file says plainly which part is theirs
- [ ] Deleting the machine-written part loses nothing that cannot be rebuilt
- [ ] Values in the base file apply everywhere; a channel's file overrides them
- [ ] A rebuild happens when something has been learned, not on a fixed schedule
- [ ] A conversation summary is written only once the conversation is large enough to be worth the call
- [ ] A file that cannot be parsed is reported at startup, naming the file, and does not stop the agent running without it
