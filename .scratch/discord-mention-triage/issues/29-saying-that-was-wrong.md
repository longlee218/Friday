# 29: Saying that was wrong

**What to build:** The operator can mark a classification as wrong, or as right,
without being asked to — and what the agent learns from comes only from what they
marked, never from what they simply have not looked at.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

The agent may interrupt the operator for exactly two things: it needs help, and it
wants a reply approved. Judging a classification is neither, and a notification per
classification is one they would answer "yes" to fifty times a day and then stop
reading — at which point the signal is dead and the examples keep growing from things
nobody looked at.

So the signal has to be one the operator gives when they feel like it. A reaction is
already how people say things in Discord, it arrives over a connection that is
already open, and it costs nothing when unused.

**Silence is not approval.** This is the fourth place in this system where an agent
would otherwise learn from its own unreviewed output — after the voice it writes in,
the observations it records, and the facts it might extract. Only a classification
marked *right* becomes an example. One that was never marked is one nobody read.

- [ ] The operator can mark a classification wrong, and can mark one right, from Discord
- [ ] Marking one has no effect on the message it concerns — nothing is re-sent or undone
- [ ] What was marked, by whom and when is recorded
- [ ] Examples given to the classifier come only from ones marked right, never from ones merely unmarked
- [ ] The operator can supply examples by hand, and those are used whether or not anything has been marked
- [ ] Marking the same thing twice, or unmarking, leaves a sensible record rather than a duplicate
