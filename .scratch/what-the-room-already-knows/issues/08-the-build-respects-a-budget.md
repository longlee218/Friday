# 08: The build respects a budget

**What to build:** A build stops growing without limit — by budget, not by
message count, because a count cannot tell twenty short messages from twenty
long ones. This is the ticket where the three compaction paths meet: a field the
schema names goes to the task's parameters, verbatim material goes to an
artifact, and only prose reaches a model.

**Blocked by:** 04, 06, 07

**Decisions:** D5, D6, D7, D8

**Status:** done

- [x] Budget is configured per phase in estimated tokens, and the configuration
      says it is an estimate — there is no tokenizer for the provider this
      system calls
- [x] Message count remains a secondary upper bound, never the primary trigger
- [x] An unset budget means no compaction at all
- [x] A budget clause that cannot be evaluated fails at startup and is never
      silently dropped — the failure shape that emptied five mechanisms here
- [x] Compaction splits three ways, and a model only ever summarises prose
- [x] A schema field already extracted is not re-read from the transcript
- [x] Two compactions that did not bring the build under budget stop compaction
      being attempted for that conversation, with a cooldown, and the condition
      is visible to the operator rather than a repeating cost
- [x] No rolling per-exchange summarisation: it rewrites the prompt prefix every
      pass
- [x] A test drives two ineffective compactions and asserts the third is not
      attempted
- [x] Guards deleted once and watched go red

## Comments

**A scope decision made explicit before writing any code, not discovered
partway through: compaction here is deterministic truncation, not a model
call.** D8 reads "a model only ever summarises prose", which implies a
model compacts what does not fit. Put to the operator directly, because it
changes both the size and the nature of this ticket: truncate-only means no
new agent, no new cost, and no data lost except by omission; model-based
compaction means a real per-task cost every time a build exceeds budget,
for a mechanism this same board otherwise keeps free of rolling
summarisation (D24, and Hermes' own reason for the same choice: "every
pass rewrites the prompt prefix and breaks the provider prompt cache").
The operator chose truncation. Consequence, followed through rather than
quietly softened: nothing here ever paraphrases a message. Compaction
drops whole messages, oldest first, and D8's "a model only ever
summarises prose" is satisfied vacuously — no model touches this path at
all, the same way none touches an artifact's content.

**The three-way split names two paths this ticket did not have to build.**
"A field the schema names is compacted into the task's parameters" and
"code becomes an artifact" were ticket 07's and this ticket's own
extractor-schema change, and the extractor's raw-text path already
carries code verbatim (ticket 07's own "current task inlined whole"). What
this ticket actually adds is the third path's boundary condition — prose
that does not fit a budget — and the parameter half, which turned out not
to exist yet: `build_input` listed every field regardless of whether
`task.params` already had it, so a task with three of four fields answered
still paid for four lines of schema on every pass. Fixed by threading
`known: Params` through `Extractor.would_ask` → `run` → the module-level
`extract` → `input_fingerprint` → `_remembering`'s wrapper → `prepare`'s
own call — five signatures, all keeping their existing default (`None`)
so no other caller changed behaviour. `ExtractionMark`'s own docstring
had claimed "the task's parameters ... never reach the extractor's
prompt", which this makes false; corrected in the same commit, in both
copies (the domain dataclass and the schema table), per CLAUDE.md's own
rule about a load-bearing decision going stale silently.

**The budget is node 0's only, not a shared config knob.** Triage already
has its own bounded design from ticket 09 (turn + summary, no chars
budget needed); the channel summariser already has `summary_max_chars`.
`context.extraction_budget_tokens` is the one phase that had no budget at
all — `original_text_for`'s `limit=20` was the sole bound, a raw message
count that cannot tell twenty short messages from twenty long ones, which
is D6's own example verbatim. `budget_tokens` is closed over by
`prepare_node` at graph-construction time (one number in `config.yaml`
serving every task type, the same "one block, not one per type" argument
ticket 03 already made for `extractor:` config), not read per call from
`DAGDeps` — there is exactly one such graph-construction-time knob
(`on_ready`) already, and this is the same kind of thing.

**The cooldown counts per task, not per conversation.** D6 says
"conversation"; this codebase already tracks node-0-adjacent state
(`ExtractionMark`) per `task_id`, and a task maps to exactly one
conversation for the whole of node 0's own working lifetime — the same
scoping choice that table already made. A new `compaction_state` table
rather than a column on `ExtractionMark`: the ineffectiveness count has to
survive independently of whatever `mark_extraction` does on the same pass
(a full-row overwrite would have reset it), and the two are genuinely
different lifetimes — one is node 0's memo of its last extraction, the
other is a standing flag that a task's own material cannot be brought
under budget.

**Guards, deleted once and watched go red, synchronously — eight of
them:** the budget-value validation at config load, the truncation loop
itself, the cooldown threshold, `_prepare`'s cooldown gate, the known-field
schema filter (twice — the filter itself, and its truthy-not-`is not None`
rule against an empty string), and the truncation floor that must never
drop the last surviving message. Each caught its own test and nothing
else, restored and diffed against a pre-mutation backup before the next.

**The classifier evaluation.** Not run. Nothing in `friday/triage/`'s own
files changed, and nothing this ticket touched (`original_text_for`,
`build_input`'s schema, `ExtractionMark`) is on triage's read path — triage
was already moved off `original_text_for`'s family of methods by ticket 09.
Same judgement as tickets 01, 07 and 10.

**Suite: 1064 passed, 1 skipped** (up from 1045 before this ticket — 19 new
tests, across `tests/test_config.py`, `tests/test_pool.py`,
`tests/test_extraction.py` and `tests/test_dag_prepare.py`).

## Review

Two-axis review against `4a86fd4` (ticket 07's commit). Standards found no
hard violations and independently traced the five-hop `known` chain hop by
hop, confirming it reaches `build_input` in the real call path with no
silent drop.

**Spec found two real gaps, both about the checklist's own words rather
than about correctness.**

**"Message count remains a secondary upper bound" had no test proving the
bound still binds.** Every budget test used fewer than `limit` messages, so
nothing distinguished "the count cap still applies" from "it happened not
to matter here." Added
`test_the_message_count_cap_still_binds_with_a_generous_budget`: 29
messages, a million-token budget, and the returned text still stops at 19
of them — mutation-verified by removing `original_text_for`'s `.limit()`
entirely and watching the test catch it.

**"Visible to the operator" was only a log line.** True, but this board's
own first law (D2) is producer *and* consumer, and a warning nobody is
necessarily watching is a weaker consumer than the board routes ticket 10
built for a full memory channel. Added `GET
/api/tasks/{task_id}/compaction` — `{ineffective_count, on_cooldown}` —
matching the existing per-task route family (`/model-calls`, `/calls`), a
`TaskCompaction` interface in `web/src/api-types.ts`, and a contract test.
No new UI widget: the checklist asks for visibility, not a screen, and no
other ticket on this board wires one — the same scope boundary ticket 07
drew for artifacts. Mutation-verified by hardcoding the route's
`on_cooldown` to `False` and watching the contract test catch it.

Spec's judgement on item 5 (D8's "a model only ever summarises prose")
was that the truncate-only scope call is legitimate but non-literal, and
should be stated as a decision rather than left to a Comments footnote —
it already was, both in this ticket's own Comments above and in CLAUDE.md's
new paragraph; no further change made there.

Full suite re-run after both fixes: 1066 passed, 1 skipped. `web/`'s
`tsc --noEmit` and `npm run build` both clean.
