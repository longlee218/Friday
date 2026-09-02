# 07: A patch waits for the operator, and resumes where it stopped

**What to build:** Applying a code change stops the run at the call, not after
it. The operator approves — possibly tomorrow, possibly after a redeploy —
and the run continues from that point rather than starting the graph again.

**Blocked by:** 06

**Decisions:** D15, D8

**Status:** done

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

- [x] The patch-applying tool is marked as needing approval; the run stops at
      the call rather than after it
- [x] The run's state is stored on the task's existing graph-state row, in a
      new nullable column, alongside the node that is waiting
- [x] Approving resumes the run in whatever process is up — including after a
      restart or a redeploy — rather than re-running the graph
- [x] Declining ends the run as a hand-over carrying the reason
- [x] A migration adds the column, runs transactionally, and
      `tests/test_migrations.py` still says the models and the migrations
      describe the same database
- [x] Reading logs and locating code still need no approval (D17)
- [x] Driven at the scripted-model seam: force the tool call, assert nothing
      was applied until an approval landed
- [x] Exempt from the byte-identity rule — D15 changes behaviour

## What it came to

A new local tool, `apply_fix(diff)`, declared `@tool(needs_approval=True)`
alongside `answer`/`hand_over` in `friday/dag/api_issue.py`. Calling it does
not write anything — the target repository is the operator's, reached over
MCP, not local disk — it returns the diff itself, so an approved call's
result is exactly the diff, which flows into `compose_reply` as "the fix"
the same way a plain-text answer would have before this ticket. Approval
gates "this diff reaches the operator's reply," not an autonomous write.

`Harness` grew two methods on top of `run()`: `checkpoint(result)` serializes
`result.to_state()` to JSON, and `resume(interruption, context=...)` rebuilds
a `RunState` against a *fresh* agent object, approves the one pending item,
and continues. Two SDK behaviours had to be discovered before either could be
trusted: `max_turns` is baked into the state at the *original* call, so a
value passed to `resume()` is silently ignored — the initial `agent.run(...,
extra_turns=2)` call has to carry enough headroom for both halves of the
interaction up front. And a dataclass-shaped context does not restore itself
on `RunState.from_json()` — it needs `context_override=<fresh instance>`, and
the SDK logs a warning regardless once `to_json()` has run, which is
cosmetic and unavoidable, not a sign anything failed.

`dag_state` gained one nullable `interruption` JSON column, alongside a
`stored["paused_at_node"]` value it already had. `HandOver` gained an
`interruption: dict | None` field, `None` for every ordinary hand-over.
`WorkflowRunner._act` split into `_act` + `_route`, so `decide_pending_action`
— the operator's yes or no — can share the same "given a decision, do what it
says" tail as an ordinary pass. Declining builds a `HandOver` without asking
the model anything further; approving resumes, and — via the new
`_continue_from` — replays the rest of the graph from exactly the node that
paused, using `resumed_value` (an `Action` if the resumed call itself made
one, otherwise its text) as that node's result, the same way any node's
result feeds the next one.

One genuinely new state-machine transition fell out of this:
`NEEDS_HUMAN -> REVIEW` and `NEEDS_HUMAN -> WAITING_FOR_DETAILS`, since
approving or declining now resolves straight into whichever of those the
resumed run produces, without a round trip through `PENDING`. Mutation-tested
by removing it and confirming `IllegalTransition` fires.

`apply_fix` is one of the tools `fix_bug`'s agent is configured to
`stop_at_tool_names` on — which means once it is approved and executes, its
own return value becomes the run's `final_output` immediately; the model
never gets a further turn to say anything about it. This was learned the hard
way: a test originally asserted a second scripted turn ("noted") would run
after resuming, and it never did — not a bug, but exactly the tool's
designed job, matching how a non-interrupted `apply_fix` call already worked
before this ticket.
