# 29: Saying that was wrong

**What to build:** The operator can mark a classification as wrong, or as right,
without being asked to — and what the agent learns from comes only from what they
marked, never from what they simply have not looked at.

**Blocked by:** None (can start immediately)

**Status:** done

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

- [x] The operator can mark a classification wrong, and can mark one right, from Discord
- [x] Marking one has no effect on the message it concerns — nothing is re-sent or undone
- [x] What was marked, by whom and when is recorded
- [x] Examples given to the classifier come only from ones marked right, never from ones merely unmarked
- [x] The operator can supply examples by hand, and those are used whether or not anything has been marked
- [x] Marking the same thing twice, or unmarking, leaves a sensible record rather than a duplicate

## Review fixes (after QA)

- **The feature was silently dead.** `on_reaction_add` only dispatches when
  the message is still in the library's RAM cache — a deque filled solely by
  live MESSAGE_CREATE, empty at every restart, and never touched by the REST
  sweep. Verified in the vendored library
  (`discord/state.py:2184-2194`): raw first, then `_get_message()`, and the
  rich event only on a cache hit. So marking anything older than the last
  restart did nothing, with no log and no error anywhere. Moved to
  `on_raw_reaction_add` / `on_raw_reaction_remove`.
- **Removing an old reaction deleted a newer mark.** ✅ then ❌ then removing
  the ✅ left no verdict at all: Discord leaves both on the message, and
  adding the new one before removing the old is the natural order to do it in.
  The removal now only clears when the mark taken back is the one on record.
- **`needs_human` leaked into the examples.** `mark_triaged` also records the
  *state* a message ended in, and marking one of those right is a sensible
  thing to do — it just must not teach the classifier a label it has no tool
  for. `CLASSIFIABLE` is a closed set, not merely "not null".
- **👍 and 👎 are gone.** A thumbs-up is the most ordinary reaction on
  Discord, and every casual one landing on a classified message would quietly
  become a training example — the exact thing this ticket exists to prevent.
- **Eight of eight examples could all be `skip`.** Marks arrive in bursts, and
  newest-first with a limit of eight makes an afternoon spent confirming a
  noisy channel into a classifier that skips. The slots are now shared across
  the types present; recency still orders within a type.
