# 08: The context rebuild stops riding a pass that can never fire

**What to build:** Separate the channel summary from the promotion pass, and
decide what happens to the observation tier that has no producer.

**Blocked by:** None (can start immediately)

**Decisions:** none yet — the second half is an open question, see below

**Status:** todo

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

- [ ] The channel summary rebuild runs on its own condition, not on
      `promoted`, and that condition is about the channel
- [ ] An `agents.summary` block exists in `config.yaml`, or its absence is
      logged loudly at startup the way an empty `sensitive_words` already is
- [ ] A test fails if `rebuild_all` becomes unreachable again
- [ ] The observation tier is either given a producer or removed, and
      `CLAUDE.md` matches whichever happened
- [ ] Each guard is deleted once and watched go red
