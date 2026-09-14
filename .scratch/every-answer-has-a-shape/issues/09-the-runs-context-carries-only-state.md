# 09: The run's context carries only state

**What to build:** the guard that closes the loop. The SDK's per-run context
means one thing from here on — the run's state — so the slot cannot again mean
"who is this run about" for one agent and "where the answer will appear" for
another (D8, D30 of the stories). No tool writes into it; every agent that takes
a context takes the state.

**Blocked by:** 06, 07, 08.

**Status:** done

- [x] No tool writes into the run context, and a test reads the syntax rather than trusting the prose.
- [x] Every agent built with a context type is built with the state's type.
- [x] The capture classes are gone, along with the tests that knew how an answer travelled.
- [x] CLAUDE.md describes the one meaning the slot now has.

## Comments

**Most of what this guards was already true when it started**, and that is the
honest shape of this ticket: tickets 07 and 08 took the captures with them, so
nothing wrote into the run context and every `context_type` was already
`FridayState`. What was missing is the thing that says so — and a rule that was
only written down is the one this repo keeps finding has drifted.

`tests/test_run_context.py` holds two guards, and they close different doors.
Every agent that declares a context type declares `FridayState`, which is
frozen — so an agent built that way *cannot* be written into, closed by
construction rather than by anybody remembering. But `context_type` is
optional, so an agent can still be handed any object through `run(context=...)`;
the syntactic half covers that.

**Two real things the guard found rather than confirmed:**

- The responder declared `FridayState if self._has_memory else None`. That was
  right while the context meant one thing — where the memory tools read their
  scope — and stopped being right when the recording sink started reading the
  message and the task off the same object (D8). It was the last place the
  slot'"'"'s meaning depended on how the agent was built. The test in
  `tests/test_responder.py` that asserted the *old* property is reversed, with
  the reversal written down rather than quietly relaxed.
- `tests/test_dag.py::_fix_bug_agent` built a harness around `friday.tools.patch`
  and `friday.tools.reply` — modules that went with the five-node `api_issue`
  graph — so it had been unimportable for as long as those had been missing,
  and nothing called it, so nothing said so.

**A third guard was written and deleted.** It flagged any class named
`*Capture`, and fired on two `Model` subclasses in tests that capture a
*prompt*: the word is not the pattern. The pattern itself is caught the moment
it matters by the two that remain — a capture nothing writes into and nothing
declares as a context type is harmless dead code, and the instant it is wired
it is one or the other. A fuzzy guard that fires on innocent code teaches a
reader to edit the guard.

**And the guarantee is the construction, not the syntax.** Probed rather than
assumed: the syntactic scan catches `ctx.context.x = y` and misses aliasing
first, `setattr`, and in-place mutation of a field. The first two raise
`FrozenInstanceError`; the third cannot arise while every field is immutable —
which was true only by accident of the field types until
`test_every_field_holds_something_that_cannot_be_changed_in_place` asserted it.
The holes are now stated in the guard file rather than left to be discovered.

That new test was itself wrong on its first write, and its own mutation found
it: `get_args(list[str])` is `(str,)`, so a `list` field looked exactly like a
`str | None` one and passed. It asserted nothing about the case it exists for.
A union has to be taken apart and a container must not be.

Mutations, each red: a tool assigning through `ctx.context`; a context type
that is not the state; a conditional picking a different type on one branch —
which the first version of that check would have *passed*, because it walked
every `Name` in the expression and subtracted `FridayState`, so the value has
to *be* the type rather than contain it; and `list`, `dict` and `set` fields on
the state, with `tuple` and `frozenset` confirmed still accepted.
