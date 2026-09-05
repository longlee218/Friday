# 08: The context rebuild stops riding a pass that can never fire

**What to build:** Separate the channel summary from the promotion pass, and
decide what happens to the observation tier that has no producer.

**Blocked by:** None (can start immediately)

**Decisions:** none yet — the second half is an open question, see below

**Status:** in progress — the rebuild is fixed; the dead tier is the operator's call

## Why

`ContextRebuilder.rebuild_all` has exactly one caller,
`friday/ops/liveness.py:71`, and it sits behind `if promoted:`. `promoted` is
`Promotion.run_once()`'s return, which counts staged observations that were
corroborated by an approved task.

Nothing writes a staged observation. `friday/tools/__init__.py:19` records why:
*"`remember` went because nothing gave it to an agent."* `Database.record_observation`
(`friday/store/db.py:480`) has no caller in `friday/`. So `observations` is
always empty, `run_once` always returns 0 at its first branch, and the rebuild
never runs.

The rebuild does two things and only one of them depends on promotion:

```python
derived["learned"] = learned                      # depends on promotion
summary = await self._maybe_summarize(channel_id) # depends on message volume
```

The second is the per-channel summary every later prompt for that room reads.
It is gated on a condition that has nothing to do with it. The result is that
`context/*.yaml`'s `derived` section is only ever written by hand through
`init_channel.py` — which is not what `CLAUDE.md` says, and nothing fails when
it is wrong.

A second switch is off in the same place: `config.yaml` has no `agents.summary`
block, so `_maybe_summarize` returns `None` immediately even when called. Both
have to move or neither is observable.

The dead tier itself is a separate decision, and it is the operator's:

- **Give `remember` back to an agent.** The staging tier and the corroboration
  threshold were built to make that safe, and they have never been exercised.
- **Remove `observations`, `Promotion`, `CORROBORATION` and `_with_notes`.** A
  subsystem with no producer for months is evidence about the need, and a
  rebuilt one would start from a real use rather than a guess.

Whichever it is, `CLAUDE.md`'s memory section describes behaviour that does not
happen and must be corrected in the same commit — this file is the one that
goes stale silently.

## Acceptance criteria

- [x] The channel summary rebuild runs on its own condition, not on
      `promoted`, and that condition is about the channel
- [x] An `agents.summary` block exists in `config.yaml`, or its absence is
      logged loudly at startup the way an empty `sensitive_words` already is
- [x] A test fails if `rebuild_all` becomes unreachable again
- [ ] The observation tier is either given a producer or removed, and
      `CLAUDE.md` matches whichever happened
- [x] Each guard is deleted once and watched go red

## What it came to, so far

**The rebuild runs every beat and decides per channel.** It asks whether the
room has said anything since the summary it already has — `summary_of`, kept
in a `state` section *outside* `derived`, because everything in `derived` is
rendered into that room's prompts and a message id is not context. Read back
from the file rather than held in memory, so a process that restarted between
beats gets the same answer as one that did not.

**A test asserted the bug.** `test_a_rebuild_happens_only_when_something_was_learned`
pinned "nothing promoted, nothing rebuilt" as deliberate, and it was green for
months while the machine-written half of every channel file was only ever
written by hand. It says the opposite now, and its docstring says why it
changed.

**And my own new test lied to me first.** Its scripted model reached into
`ScriptedModel`'s internals to count calls, which broke the summary on every
pass — so the test read "no second summary" as the behaviour it was checking
for, when the real answer was "no summary at all, twice". A counting model
written out rather than wrapped around one.

## Still open, and not mine to decide

Whether the observation tier is **given a producer or removed**. The operator
has already chosen its replacement — the four memory tools of ticket 09 —
which makes removal the consistent answer, and removal means dropping two
tables that no code writes to. That is destructive and irreversible in a way
the rest of this ticket is not, so it waits for a yes.

Until then `Promotion` still runs on every beat over a table nothing writes,
which costs one query a minute and is honest about achieving nothing.
