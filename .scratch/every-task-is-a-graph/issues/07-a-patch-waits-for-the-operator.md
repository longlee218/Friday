# 07: A patch waits for the operator, and resumes where it stopped

**What to build:** Applying a code change stops the run at the call, not after
it. The operator approves — possibly tomorrow, possibly after a redeploy —
and the run continues from that point rather than starting the graph again.

**Blocked by:** 06

**Decisions:** D15, D8

**Status:** ready-for-agent

## Why

The one genuinely dangerous thing a node can do is guarded today by a word
list matched against the cause and the file path, and by the fixer being left
unconfigured. Both are real, and neither is a gate: the word list is a string
match on text a model wrote, and "unconfigured" stops being protection the
moment someone configures it.

Two kinds of side effect, two gates. Messages go through the outbox, which
ticket 06 leaves exactly where it is. Actions go through the SDK's own tool
approval: the tool is marked as needing approval, the run stops holding its
state, and the state goes into the checkpoint alongside the node that was
waiting. That is what makes approving in another process work, which is the
whole point — the operator is not sitting at the console when the graph gets
there.

Restarting the graph instead would be the cheap version, and it would spend
the log queries and the analysis again to arrive at the same patch.

## Acceptance criteria

- [ ] The patch-applying tool is marked as needing approval; the run stops at
      the call rather than after it
- [ ] The run's state is stored on the task's existing graph-state row, in a
      new nullable column, alongside the node that is waiting
- [ ] Approving resumes the run in whatever process is up — including after a
      restart or a redeploy — rather than re-running the graph
- [ ] Declining ends the run as a hand-over carrying the reason
- [ ] A migration adds the column, runs transactionally, and
      `tests/test_migrations.py` still says the models and the migrations
      describe the same database
- [ ] Reading logs and locating code still need no approval (D17)
- [ ] Driven at the scripted-model seam: force the tool call, assert nothing
      was applied until an approval landed
- [ ] Exempt from the byte-identity rule — D15 changes behaviour
