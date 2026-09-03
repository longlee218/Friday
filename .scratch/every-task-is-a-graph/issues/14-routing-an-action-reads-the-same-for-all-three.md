# 14: Routing an action reads the same way for all three

**What to build:** `_route` answers the same four questions for `Ask`, `Reply`
and `HandOver`, in the same shape, so reading one branch tells you how to read
the others. No behaviour changes.

**Blocked by:** None (can start immediately)

**Decisions:** none — this is readability at the seam the operator's messages
come out of

**Status:** ready-for-agent

## Why

Raised by the operator reading the code, not by a review: *"Ask thì gọi `_say`,
Reply thì gọi `_propose` — thiếu tính thống nhất và rất confuse."* They are
right, and the confusion has three separate sources.

**1. `_say` and `_propose` are not siblings, but they are named and called as
if they were.** `_propose` is a routing outcome — it queues rows and moves the
task. `_say` is a text transformation — template in, the operator's voice out.
One is a branch; the other is a step inside a branch. Both are verbs about
speaking, sitting next to each other in `_route`, which is what makes the
asymmetry read as arbitrary.

It is not arbitrary. `Reply`'s text was written by `compose_reply`, an agent
that already wears the Responder persona, so it is already in that voice.
`Ask`'s text was assembled by `_question()` — code, no model — so it has no
voice at all until something gives it one. `_say` exists to bring `Ask` up to
the level `Reply` arrives at. That reason is invisible from the names.

**2. `_route` tests `isinstance(action, Ask)` three times in sequence**, with
fall-through control flow between them, and a final `else` that reads
`action.reason` — reachable only because the branches above it happen to have
returned. A standards review flagged this as Repeated Switches during the
board; ticket 13's acceptance criteria did not include it and it was left.

**3. `_say` is a name this codebase already uses for something else.**
`friday/ops/liveness.py` has its own `_say`, which posts an alert. Two
unrelated methods, one name, and the reader has to hold both.

The two guards on `Ask` — the `max_asks` cap and the `auto_ask` switch — are
real and stay. They are conditions on *whether to ask at all*, so they belong
at the top of the asking branch, not as two more `isinstance` arms.

## Acceptance criteria

- [ ] `_route` has one branch per action kind and nothing falls through
- [ ] The `max_asks` cap and the `auto_ask` switch read as guards on asking,
      not as separate action kinds
- [ ] `_say` is named for what it does — giving code-written text the
      operator's voice — and no longer collides with `liveness.py`'s `_say`
- [ ] Why `Ask` needs that and `Reply` does not is written down where the two
      branches are, since the asymmetry is the thing that confused a reader
- [ ] No behaviour changes: every existing test passes untouched, and each of
      the five paths through the old `_route` is mutation-tested to prove the
      suite would have caught a change
- [ ] Every assembled prompt byte-identical — this ticket changes no text
