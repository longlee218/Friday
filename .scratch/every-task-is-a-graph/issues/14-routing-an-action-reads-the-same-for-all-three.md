# 14: Routing an action reads the same way for all three

**What to build:** `_route` answers the same four questions for `Ask`, `Reply`
and `HandOver`, in the same shape, so reading one branch tells you how to read
the others. No behaviour changes.

**Blocked by:** None (can start immediately)

**Decisions:** none — this is readability at the seam the operator's messages
come out of

**Status:** done

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

- [x] `_route` has one branch per action kind and nothing falls through
- [x] The `max_asks` cap and the `auto_ask` switch read as guards on asking,
      not as separate action kinds
- [x] `_say` is named for what it does — giving code-written text the
      operator's voice — and no longer collides with `liveness.py`'s `_say`
- [x] Why `Ask` needs that and `Reply` does not is written down where the two
      branches are, since the asymmetry is the thing that confused a reader
- [x] No behaviour changes: every existing test passes untouched, and each of
      the five paths through the old `_route` is mutation-tested to prove the
      suite would have caught a change
- [x] Every assembled prompt byte-identical — this ticket changes no text

## What it came to

`_route` is three lines and three branches — `Reply` to `_propose`, `Ask` to a
new `_ask`, everything else to a new `_hand_over`. Nothing falls through, and
`isinstance(action, ...)` appears twice in the whole file instead of three
times in one function.

The two guards moved into `_ask`, where they read as what they are: conditions
on whether to ask *at all*, settled before anything is worded. Their order is
unchanged — the cap is still checked before `auto_ask`, so the query that
counts previous asks still runs in both cases.

`_say` is `_in_the_operators_voice`. Two reasons beyond the name being vague:
`friday/ops/liveness.py` has its own unrelated `_say` that posts an alert, so
one name covered two things; and the new name states the job rather than
implying it is the peer of `_propose`, which it never was — `_propose` is a
branch, this is a step inside one.

The asymmetry that prompted the ticket is now written at both ends. `_route`'s
docstring carries the table (who reads it, whether anything rewords it, how
many rows, which state), and `_in_the_operators_voice` says why only `Ask`
comes through it: a `Reply` was written by `compose_reply`, an agent already
wearing the Responder persona, so it arrives in that voice; an `Ask` came out
of `_question()` with no voice at all. Bringing asking up to where answering
starts is not treating them differently.

CONTEXT.md gained the sentence whose absence caused the confusion in the first
place: it said `Reply` waits for approval without ever saying who reads it.
`Ask` and `Reply` both go to the reporter — `Reply` waits *because* it answers
them in the operator's name — and `HandOver` is the one addressed to the
operator.

**The net was proven twice.** Before touching anything, each of the five paths
through the old `_route` was mutated and watched go red against a named test;
after the rewrite, the same five mutations against the new shape were caught
by the same five tests. Prompts byte-identical. 642 tests pass, the same 642
as before — this ticket adds no behaviour and removes none.
