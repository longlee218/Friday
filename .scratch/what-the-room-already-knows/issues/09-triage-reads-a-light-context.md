# 09: Triage reads a light context

**What to build:** Triage is shown this person's turn and a short summary of the
room, instead of every relevant message the conversation has ever held. Today it
receives an unbounded window — 24 lines over seven days in the recorded flow,
growing forever — to decide one label and one number.

This reverses ticket 26, which chose the unbounded window deliberately for
prompt-prefix stability and has three tests asserting it. What that bought is
real; what it cost is 2,552 characters of transcript in the highest-volume
prompt and a hallucinated colleague. The tests move up a layer rather than out.

**Blocked by:** 02, 03, 06

**Decisions:** D2, D3, D24

**Status:** done

- [x] Triage's input is the turn plus the room's summary, under the light budget
- [x] Domain memory, task parameters and artifacts do not reach triage — it
      decides a label, not a value
- [x] The three tests that pinned the unbounded window are rewritten one layer
      up: a summary stable between calls when the room has said nothing new, so
      prefix stability is still asserted where it is now true
- [x] The reversal of ticket 26 is stated in this ticket and corrected in the
      file that records what exists, in the same commit
- [x] A reporter's reply to something this system asked still reaches the task
      that asked, by the id lookup that already does it and not by prompt
      context
- [x] Triage's tool schema is unchanged: no project, no topics, no entities
- [x] **The frozen set gains rows this ticket can actually be measured by**,
      before the evaluation is run. As shipped it holds 16 single-message rows
      with no newlines and no `is_own`, and `run_triage_eval` passes no
      `context=` — so it exercises neither a window, nor deduplication, nor
      the ownership mark, nor the line-forgery defence. Ticket 02's run
      returned the recorded baseline to the digit for exactly that reason.
      A ticket that changes what triage is shown, measured by a set that
      shows it one line, is measured by nothing
- [x] The evaluation is run against the frozen set, and accuracy, the confusion
      matrix and the threshold table are reported alongside the change
- [x] Guards deleted once and watched go red

## Comments

**What changed, mechanically.** `Triage.decide` no longer takes
`context: Sequence[InboundEvent]` (the unbounded window). It takes `turn:
Sequence[InboundEvent] = ()` instead — the raw messages `TriageRunner._triage_one`
already reads via `turn_from`, passed through rather than pre-joined into one
string — and resolves the room from a context store it now holds, the same
split ticket 01 gave the extractor. `friday/triage/prompt.py::build_input`
renders `channel_derived(room)` — the same section the responder already
reads — ahead of the turn. `friday/store/db.py::relevant_messages` is
**untouched**: `Pool` (`friday/tasks/pool.py:326`) still calls it for the
responder, which was always its other caller (`ticket 26`'s own closing note
says so — "`TriageRunner` and `WorkflowRunner._say` both read through
`relevant_messages` now"). Only triage's own call to it is gone.

**Verified concretely, not just by the suite.** Rendered the actual eval row
that mirrors the flow that started this board — three messages, every one
`is_own`, one line reading "em là Nhím":

```
[05:21 | this account] eval: a ơi kiểm tra api hộ em với
[05:21 | this account] eval: Hi a, em là Nhím, e đang ghép API của a nhưng đang bị lỗi
[05:21 | this account] eval: correlationId nằm trong response header x-request-id đó
```

Every line carries the mark now. The live eval scored this row `api_issue`,
correctly.

**Eval: 94.4% (17/18), up from the 93.8% (15/16) baseline — same single row
wrong, both new rows correct.**

```
confusion (rows: expected, columns: predicted)
                access_request  api_issue  doc_question  needs_human  skip
access_request  4               0          0             0            0
api_issue       0               6          0             0            0
doc_question    0               0          3             1            0
needs_human     0               0          0             0            0
skip            0               0          0             0            4

0.5: 1/18   0.6: 1/18   0.7: 1/18   0.8: 1/18   0.9: 4/18
```

The sixteen original rows score identically to the pre-ticket-09 baseline —
same `doc_question`→`needs_human` miss, same cell. The two new rows (a
three-message burst with a line-forgery attempt, and the is_own self-test row
above) both landed correctly on `api_issue`. At `confidence_threshold: 0.7`
(what production actually uses), the escalation count is unchanged: 1/16 →
1/18, same row. The 0.9 column growing (2→4) is the two new rows sitting
between 0.7 and 0.9, not a regression at the threshold that matters.

**The eval set gained two rows, and a producer for them, per D2.** `Example`
gained an optional `turn: tuple[tuple[str, bool], ...]`, omitted from disk
entirely when empty so the sixteen existing rows stay byte-identical single
strings. `build_frozen_set`'s `seed` accepts a richer `(text, expected, turn)`
triple for exactly this — `confirmed` verdicts never carry one, since a stored
classification has no such shape. `evals/build_triage_set.py`'s `SEED` is the
producer: the two new rows live there, so a future refresh from live data
does not silently drop them.

**Deduplication has no live production scenario for triage any more, and I
did not force one into the eval.** Ticket 02 built dedup because the OLD input
was `window + [joined_turn]`, where the joined turn's own message id also
appeared inside the window. Removing the window removes the overlap it
guarded, not just the coverage: `turn_from` reads one author's distinct
messages, so a real turn cannot contain a duplicate id. The mechanism's own
unit tests (`tests/test_prompt_sections.py`) remain its coverage in general;
manufacturing an eval row to duplicate an id that can no longer occur would
have tested a scenario, not a property.

**Two things found while this file was open, neither part of ticket 09's own
work but directly forced or exposed by touching it:**

`friday/triage/prompt.py::build_instructions` still called `skill_system
(skills_meta)` behind a comment claiming "triage gets skills like every other
agent now" — ticket 03 removed `Triage.__init__`'s `skills=` parameter, so
`skills_meta` was always `None` at the one call site and the render was always
a no-op, but the parameter and the stale comment survived because ticket 03
never touched this file. Removed; the docstring says why.

`test_triage_reads_the_conversations_context`'s only assertion —
`assert triage.seen` — never checked what triage was shown, only that
`decide` was called. Its premise (an unbounded window reaching triage) is
exactly what this ticket removes, so it is replaced by
`test_triage_is_given_the_turn_not_the_unbounded_window`, which asserts on
the actual turn content and confirms a message from before the turn started
does not leak into it.

**A weak assertion mutation testing caught, the same pattern this session
keeps finding.** The first version of `test_decide_renders_the_given_turn_not
_just_the_one_event` only asserted `isinstance(outcome, Decided)` — true
whether or not `turn=` was ever read, since the scripted model does not
inspect its input. Mutating the turn-fallback (`list(turn) or [event]` →
`[event]`) passed the whole suite. Rewritten to capture and assert on what the
model actually received.

**Guards, each deleted once and watched go red:** `channel_derived` dropped
from `build_input`, the turn fallback removed, the room hardcoded (scope
leak between channels), the runner no longer forwarding `turn`, the eval no
longer passing `turn=`, and 3-tuple seed support removed from
`build_frozen_set`. Six mutations, six catches — the sixth only after the
weak assertion above was fixed.

**Suite: 1002 passed, 1 skipped.**

## Review

Two parallel reviews (Standards, Spec) ran against the full uncommitted diff.
Both independently confirmed the two claims flagged for scrutiny —
`relevant_messages` and its three tests genuinely untouched, still serving the
responder via `Pool`; the two cleanups genuinely forced by this ticket's own
edits to those exact functions — and confirmed every remaining acceptance
criterion, including the ones easiest to half-do (no domain memory/base/
overrides reach triage; the reply-to-a-question lookup untouched;
`friday/tools/classify.py` untouched). Four real findings, all fixed:

**`friday/triage/prompt.py` typed `room: "ChannelContext | None"` with no
import of `ChannelContext` anywhere in the file.** Harmless under
`from __future__ import annotations` — nothing evaluates the annotation at
runtime — but an unresolvable forward reference to anyone reading the file or
running a type checker, and inconsistent with `room_facts`'s own pattern of
importing the name before quoting it. Added the import; the quotes are no
longer needed either, so they came off too.

**`evals/README.md`'s cost estimate now describes a set two rows narrower
than the real one.** It still said "the shipped 16-row seed set" and priced a
run against that count; `SEED` is 18 as of this ticket. The honest fix is not
scaling the old number by 18/16 — the two new rows are each a multi-message
turn, larger than the average of the sixteen the old figure was measured
against — so the file now states the row count accurately and says plainly
that the token estimate predates the change and was never re-measured
(`run_triage_eval.py` records no per-call cost at all), rather than presenting
a scaled guess as a measurement.

**CLAUDE.md's new paragraph overstated what the third prefix-stability test
actually measures.** It said the shared prefix was "over 90% of what a
provider actually sees" — the test concatenates `Triage(...)._run.agent
.instructions` with `build_input(...)`, which omits the real wire format's
envelope and tool-schema bytes. Since those are identical between the two
calls being compared, the computed ratio is a conservative *lower* bound on
the true figure, not an inflated one — but the sentence claimed more precision
than was measured. Corrected to say what was actually computed and why it is
still a fair bound.

**The first prefix-stability test proved a different, weaker claim than its
own docstring stated.** It built one `ChannelContext` object, called
`build_input` against it, called `build_input` a hundred more times against
the *same* object with throwaway events that touched no database, and called
that "the room has said more" — it proved `build_input`/`channel_derived` are
pure functions of their argument, which is true and worth knowing, but never
exercised a real database at all, so it said nothing about what happens
between two calls when the room genuinely has said more and no rebuild has
run. Rewritten to use a real `ContextStore` and a real `db`: it records real
messages into the database with no `rebuild_all()` in between, and confirms
`store.context("watched")` — what `build_input` actually reads — has not
moved. That is the honest version of "one layer up" from ticket 26's own
database-level test, and it is a property that holds structurally now (a
channel's context file and the `messages` table are two stores nothing
connects on read), which is worth stating rather than assuming.

**Suite after all four fixes: 1002 passed, 1 skipped** — unchanged in count,
since three of the four fixes touch documentation and the fourth rewrites an
existing test rather than adding one. Nothing in these fixes touches the code
the eval measured, so the 94.4% result stands as reported above.
