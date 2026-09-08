# 04: Node 0 stops paying twice for the same extraction

**What to build:** A task waiting for details in a busy room stops re-billing an
identical extraction on every pass. One task in the recorded data has two
extractor calls of 1,790 input tokens whose prompts are byte-identical — node 0
is excluded from the checkpoint and re-executes every pass, correctly, because
it must see messages that arrived since the last one. What it must not do is
call a model when nothing arrived.

Independent, and early on purpose: it is a pure cost fix, verifiable against
the flow already recorded, and it stops money leaking while the rest is built.

**Blocked by:** None (can start immediately)

**Decisions:** D23

**Status:** done

- [x] Node 0 compares before calling. **This criterion was wrong and the code
      does not follow it:** the parameters do *not* count, because they never
      reach the extractor's prompt — its per-call input is the field schema
      and the reporter's text. The fingerprint is over those two. See the
      Comments below and
      `test_a_parameter_change_the_extractor_cannot_see_is_not_paid_for`
- [x] Nothing changed — no model call, and the previous result stands
- [x] Something changed — extraction runs, exactly as today
- [x] Node 0 still sees a message that arrived since the last pass, which is the
      reason it is excluded from the checkpoint in the first place
- [x] A test drives two passes with nothing new between them and asserts one
      model call, not two
- [x] The guard is deleted once and watched go red

## Comments

**Criterion 1 was wrong, and the test that documents the deviation says so in
its name.** The extractor's per-call input is `build_input(text, params_cls)` —
the field schema and what the reporter wrote. Task parameters never reach it,
so fingerprinting them would pay again for a change the extractor cannot see.
Both reviewers verified the claim against the code and agreed the criterion
should change rather than the implementation. A parameter change still changes
what node 0 concludes: the replayed question is re-filtered against the
parameters as they are now, so a field the operator filled suppresses it with
no model call.

The schema *is* in the fingerprint, which the criterion did not ask for either:
adding a field or rewording what one means changes the prompt, and a task
already marked would otherwise never be read again under the new one.

**Two things review found that the tests written with the feature did not.**

First, a real bug: a failed extraction was memoised as an answer. `(None,
None)` from `Extractor.run` is not "the model found nothing" — a model that
finds nothing still returns a `Params` with every field absent — it is the
harness having swallowed a provider error into `last_error`. Marking that would
turn one 502 into a task never read again, against this repo's own "a hiccup is
retried here and nowhere else". Now nothing is written unless there is
something to remember, with a test.

Second, the deduplication in ticket 02 was keyed on `provider_message_id`
alone, where the documented identity of an inbound message is `(provider,
provider_message_id)`. Fixed, with a test.

**Prose that had already drifted, in three places, before the feature shipped.**
The fingerprint was described as "the reporter's text alone" in the schema, as
"text and nothing else … the schema, which is fixed per type" in the domain,
and as including the schema *because it is not fixed* in the node — three
descriptions of one value, two of them wrong. Also corrected: a claim that the
mark and the task's parameters were written in one call (they are two), and a
claim that anything clears a mark (nothing does — stated now as the deliberate
choice it is, with the reason it is not the "an approved patch outlived its
task" failure this repo has shipped once).

**`updated_at` was dropped from the table.** It was written and never read,
which is this board's own first law (D2) applied to its own code.

**Eight guards across tickets 02 and 04, each deleted once and watched go red:**
memo hit (3 failed), clarify replay, fill replay, schema in fingerprint, the
no-mark-on-failure guard, the dedup key, the ownership mark, the line defence.
Green on restore. Two of these — clarify replay and fill replay — passed the
first mutation run with the guard deleted: the clarify test never reached the
clarify path, because `_problems` runs first and `_traceable` fired, and the
fill test could not observe a replay because `_fill` only fills blanks and the
value was already persisted. Both rewritten.

**Suite: 941 passed, 2 failed, 1 skipped.** Both failures reproduce at
`ac13ea5` and are not this board's — see ticket 02's Comments.
