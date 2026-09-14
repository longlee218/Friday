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


## Review

`/code-review` against `4aab038`, Standards and Spec as parallel subagents.
Both converged on the same thing, and it is worth stating plainly: **the
syntactic guard is a net, not the closure.** Both found the alias
(`state = ctx.context` then `state.decided = x`) independently, and it is the
likeliest spelling of the three it misses, because `friday/tools/memory.py`
already opens with exactly that line — it is the shape a tool author has in
front of them. The guard's docstring and CLAUDE.md both said "pinned shut";
they now say what closes the door (frozen, with immutable fields, asserted)
and what merely watches it.

Four more, all verified before acting:

- **The scan did not cover the composition root.** `SOURCES` read `friday/`
  and `tests/` while CLAUDE.md claimed "every `context_type=` in the repo" —
  so an agent wired wrongly in `run_agent.py`, *the one place adapters are
  constructed*, was exactly what these guards exist to catch and exactly what
  they could not see. `evals/` builds a real `Triage` and was outside too.
  Both now scanned, and both mutated to prove it.
- **Two lines of CLAUDE.md still described the old mechanism**: triage
  stopping on `capture.decided is not None`, and captures travelling with a
  tool. This is the file that goes stale silently, in the ticket whose job was
  to stop that.
- **The write guard is broad on purpose** — any store through an attribute
  named `context` fires, including `friday/dag/prepare.py`'s `FullContext`.
  That is the right way round here, but it was the same false-positive shape
  this ticket rejected a third guard for, so the message now says what it saw
  rather than what it concluded.
- **Prose duplication**: CONTEXT.md's new paragraph restated its own opening
  twenty lines above, the responder gained a comment already written sixty
  lines below it, and the `_fix_bug_agent` tombstone carried a fourth copy of
  its own provenance. All trimmed.

One thing the Spec axis asked that is worth recording: with this ticket done,
is the spec's *"Nothing is read off a capture; no caller parses anything"*
true? **The first half, yes** — nothing in `friday/` reads a capture. The
second is true of every answer with a shape, and not literally true of the
responder, which still strips a reasoning block out of free text. That is D12
working as written: prose is the responder's actual product, and giving it a
shape would be a lie about what it returns.
