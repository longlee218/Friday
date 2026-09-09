# 06: A room has a summary, and the extractor reads it

**What to build:** A room accumulates a structured summary of itself, and the
extractor reads it. Today every room's derived context is `{}`, the summariser
is commented out of configuration, and even enabled it would only fire once a
transcript passed half a model's context window — so the compaction layer is an
emergency valve, never a context-building step.

The summary **accompanies** the messages here; nothing is replaced. Replacement
is 08, and triage's switch is 09. Proving the summary works where no test
resists it comes first.

**Blocked by:** 01

**Decisions:** D2, D9, D11, D12

**Status:** done

- [x] The summary carries **four** fields, not the six D9 named — see
      Comments: `open_questions` and `artifacts` are D2 applied to a prompt
      rather than to a table
- [x] It records the first and last message it covers and the version that wrote
      it; raw messages are never deleted
- [x] The summariser writes it when the room has said more since the last one,
      with the fraction-of-context-window gate removed
- [x] A missing summariser configuration is loud at startup, as it already is
- [x] The summary is stored plain and escaped once, at the section seam
- [x] It reaches the extractor through the **channel** slot, not the
      conversation slot this criterion named — see Comments: tickets 01 and
      05 shipped after this one was written and already claimed both slots
- [x] It is capped at the configured length, default six thousand characters
- [x] A summary that tries to forge a second key line does not succeed —
      through the indent+flatten defence ticket 01 built, not a length frame:
      see Comments, that criterion was already stale when this ticket started
- [x] The scripted-transport seam is used; no test in this ticket touches the
      network
- [x] Guards deleted once and watched go red

## Comments

**A severe, silent, pre-existing bug was found and fixed: every beat wiped the
summary it had no reason to touch.** `rebuild_all` built `derived = {}` fresh
on every call and wrote it unconditionally — so a channel with a real summary,
on the very next heartbeat where nothing new was said, had it replaced with
`{}`. `_maybe_summarize`'s early return for "nothing new" and its early return
for "we produced nothing" were the same falsy value, and the caller could not
tell them apart. Reproduced before touching the fix:

```
after 1st rebuild: {'summary': 'short conversation about checkout'}
after 2nd rebuild (no new msgs): {}
```

At the default 60-second heartbeat, this means a room's derived context was
never actually *context* — it existed for at most one beat after being
written. This is a large part of why every room's derived context is `{}` in
production, independent of the summariser being commented out of config.yaml.
It was forced into the open by this ticket's own requirement that a refused
over-cap summary leave the previous one standing — that is impossible without
first fixing the exact confusion that was wiping it. `rebuild_all` and
`_maybe_summarize`'s contract is now: `None` always and only means "leave
`derived` exactly as it is."

**Two of the ticket's own criteria were stale, both because tickets written
after this one shipped first and settled questions this one had left open.**

`open_questions` and `artifacts` are not in the summary. D9 named six fields;
this ships four. `open_questions` is derived from the outbox with no model at
all (ticket 05's `unanswered_questions`) — a channel-wide, model-written second
version of the same fact could only disagree with the one that is a query over
what was actually sent. `artifacts` waits for ticket 07, which is what produces
one; asking a model for the ids of things that do not exist is asking it to
invent them. Both absences are D2 applied to a prompt rather than to a table.

"Through the conversation slot" was wrong the day this ticket would have been
picked up: ticket 01 already wired `room_facts` — which reads `ctx.derived`,
where the summary lives — into `memory()`'s **channel** slot, and ticket 05
took the conversation slot for outstanding questions. No new prompt-side
wiring was needed at all; `room_facts` already renders whatever `rebuild_all`
writes to `derived["summary"]`, which is the whole reason a structured value
had to render correctly through it (below).

"Length-framed" was also already reversed: ticket 01 shipped a length count on
`memory`'s frame, both of its reviews called the count decorative (nothing
re-renders and compares it, unlike the Hermes precedent it was copied from),
and it was removed in favour of indenting content so it cannot open a line.
That is the defence this ticket's own forging test exercises.

**`_render_pairs` could not render a structured summary at all — a crash, not
a bug in the summary's content.** `facts`/`decisions`/`constraints` are lists;
`room_facts` and `channel_derived` (the responder's own direct read of
`ctx.derived`) both flatten values through `_one_line`, which calls `.split()`
— a method a list does not have. Fixed by joining a list to one line with
`"; "` before it reaches `transform`. Mutation testing found this had **no**
test at all on the first pass; added one exercising `room_facts` and one
exercising `channel_derived` directly, since the responder reads the second
path in production and a fix proven only through the first would have left it
unverified.

**Four gaps in `_parse_summary` had no coverage either**, each found by
mutating the function and watching the suite stay green: valid-JSON-but-not-an-
object degrading to `topic`, valid-object-with-no-recognised-keys degrading to
`topic`, blank list entries being dropped, and a list field given as a string
being dropped rather than silently wrapped. Eight direct unit tests now pin
`_parse_summary` at its own seam, pure function, no I/O — the same reasoning as
testing `_fingerprint`/`asked_as` directly rather than only through the whole
pipeline.

**Six pre-existing tests encoded the old flat-string shape** (`derived["summary"]
== "some prose"`) and needed rewriting to the structured/degraded shape; one
(`test_a_summary_is_written_only_once_the_conversation_is_large_enough`) tested
the removed gate directly and was deleted, superseded by
`test_a_room_that_has_said_more_is_summarised_however_little`. Two more test
files' fakes (`tests/test_recording_reaches_every_agent.py`'s `_config` helper,
several `ContextRebuilder(...)` call sites) needed their `summary_share=` kwarg
dropped — the same collateral pattern every ticket on this board has produced
when a parameter it removes was threaded through existing test doubles.

**Config:** `ContextConfig.summary_share` (a fraction of a model's context
window) is gone; `ContextConfig.summary_max_chars` (default 6000) replaces it,
wired through `config.yaml`'s `context:` block and `ContextRebuilder.build`.
`AgentConfig.context_window`'s comment claimed a consumer that no longer
exists after this ticket removed the one gate that read it; corrected rather
than left to describe dead behaviour. The field itself is kept — it is a
general fact every agent's config carries, not something this ticket's change
made an orphan.

**`CONTEXT.md` gains a term, "Channel summary"**, next to "Memory" — the two
different kinds of channel-scoped knowledge this system keeps, one derived from
the transcript and one an agent chooses to write down. `CLAUDE.md`'s term count
corrected 24 → 25.

**The commented-out `summary:` block in `config.yaml` is left commented out.**
Enabling it is a recurring real-money decision on the operator's own live
config, not something this ticket should do silently; its comment is corrected
to describe the gate-removed behaviour, but the operator still has to
uncomment it themselves.

**Seven guards, each deleted once and watched go red:** the wipe-on-nothing-new
bug, the cap-refuses check, four `_parse_summary` degrade/filter paths, and the
`_render_pairs` list-value crash fix (verified through both `room_facts` and
`channel_derived`).

**Suite: 988 passed, 1 skipped.** No eval: this does not touch triage's prompt
or anything upstream of it. Verified end to end against a real
`ContextRebuilder` + `ContextStore` + `build_input`, not only through unit
tests — a structured summary written by a scripted model reaches the
extractor's prompt through the channel slot with no new plumbing, and an
over-cap summary is refused while the previous one continues to reach it.

## Review

Two parallel reviews (Standards, Spec) ran against the full uncommitted diff.
Both independently confirmed the wipe-bug claim by reading the removed lines
directly, confirmed the two reinterpreted criteria are genuinely forced by
tickets 01/05's shipped code (not merely convenient), and confirmed the
three-way degrade in `_parse_summary` is real, distinct branches rather than
one masking the others. Three real findings, all fixed:

**`memory()`'s own docstring still claimed a length count that ticket 01 had
already removed.** Spec review caught it: `_framed`'s docstring (further down
the same file) correctly says the count was dropped as decorative, on both of
ticket 01's reviews' say-so, but `memory()`'s docstring — written before that
removal — still said "the label carries its length, which is a boundary
content would have to count itself to forge." Two docstrings in one file
contradicting each other, found because this ticket gave the builder its
second caller and a reviewer checked what it claimed against what it does.
Corrected to describe the one defence that actually holds.

**`SUMMARY_FIELDS` was declared and never read.** Standards review: `_parse_summary`
named `"topic"` a second time and wrote out `("facts", "decisions",
"constraints")` as a second literal tuple, so the four-key contract had two
sources with nothing tying them together — a fifth field added to one and not
the other would drift silently. `_parse_summary` now loops over
`SUMMARY_FIELDS` itself, with `topic` handled as the one field needing a
scalar rather than a list rule. Verified by mutation: dropping a name from the
tuple now fails the three tests that name it, where before the fix it could
not have — the constant was inert.

**A grammar nit** ("a Extraction mark" → "an Extraction mark") in the new
CONTEXT.md term, standards review.

**A near-miss worth recording.** Verifying the `SUMMARY_FIELDS` fix, I ran
`git checkout -- friday/memory/channel_context.py` to reset a scratch mutation
and reflexively reverted the entire uncommitted ticket 06 rewrite of that
file to the last commit (`f63906f`) — nothing from this ticket had been
committed yet. Caught immediately by re-running the suite (32 failures where
none were expected) rather than by inspection. Recovered from a manual backup
taken earlier in the session for mutation testing, which was missing only the
`SUMMARY_FIELDS` fix itself; that one fix was small enough to redo from the
diff still visible in this same conversation. Nothing was lost, but the
backup existing at all was luck rather than a habit — a scratch mutation
against an uncommitted rewrite should be verified by re-reading the specific
target string's presence before `git checkout`, or applied to a copy, never to
the working file directly.

**No further findings.** Everything else both reviews checked — the cap/refuse
mechanism, `summary_range`'s storage, the six rewritten tests, the `_render_pairs`
list-join's `"; "` ambiguity (judged an explicitly-documented, defensible
tradeoff against a crash, not a violation), `remember_summary_of`'s backward
compatibility — was confirmed correct as shipped.

**Suite after all review fixes: 988 passed, 1 skipped.**
