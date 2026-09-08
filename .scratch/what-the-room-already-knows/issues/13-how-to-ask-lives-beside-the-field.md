# 13: How to ask about a field lives beside the field

**What to build:** Adding a task type is writing one class. The phrase used to
ask a reporter about a field lives on that field, next to the phrase that tells
the extractor what the field means — so the two cannot drift apart, and there
is no second file to remember.

Today that phrase lives in `_ASKED_AS`, a hand-kept dict in the node that
renders the question, keyed by field name and sitting far from every field it
names. It has already drifted: `project` is askable and has no entry, so it
falls through to a fallback that happens to read acceptably (`the project`) and
therefore told nobody it was missing.

This is the same mistake the `doc` metadata was written to end. The comment
above it says so: the field meanings "lived in triage's tool docstrings until
triage stopped extracting — removing them then, instead of moving them here,
left the extractor reading a schema of `summary: summary`. **Same place as the
field, so a field and its meaning cannot drift apart again.**" `_ASKED_AS` is
that arrangement, unfixed, for a second string about the same field.

**Blocked by:** None (can start immediately)

**Decisions:** D2

**Status:** done

- [x] Every askable field carries how to ask about it, beside what it means —
      askable being what `ask_for_fields` already computes: every field except
      the ones the model writes about the message rather than reads off it
- [x] A cross-field rule carries its own phrase, because its subject is not a
      field and cannot hold one. **This reverses a decision with a docstring
      behind it** (`OneOf` currently states that phrasing lives in one place and
      that a rule wording its own question would be a second place that
      drifts); the reversal and its argument are recorded in the code
- [x] The fallback is gone. A field with no phrase fails loudly where a person
      sees it, rather than asking a reporter for "the retry after"
- [x] A test asserts every askable field says how to ask about it, and fails
      today because of `project`
- [x] A test writes out the whole set of questions this system can ask, so
      "what can it ask?" is still answerable in one place — the same answer
      this repo gave for "what can the agents do?", which was a test that
      asserts the list rather than a module that collects it
- [x] That test also asserts the split the responder's floor depends on —
      which questions name something that must survive translation — computed
      from the same pattern `friday/responder/check.py` uses, not counted by
      hand
- [x] `CLAUDE.md`'s sentence about that split is corrected in the same commit,
      because adding the missing `project` changes the count it states
- [x] Every question this system asks reads the same as it does today, except
      `project`, which gains the phrase it never had
- [x] Guards deleted once and watched go red

## Comments

**The reviews moved the resolver out of this ticket's own code, and that was
the right call.** The first version put `_asked_as` in `friday/dag/prepare.py`,
where the question is rendered. Standards review named it Feature Envy and was
right for a sharper reason than tidiness: it made a *second* module walk
`type(params)._RULES`, and it read the rules off the **instance** where
`validate` reads them off the class. `asked_as` lives in
`friday/domain/validation.py` now, beside the engine that produces the
`Problem`s whose fields it resolves. One module owns the rules and owns how to
speak about what they report.

That single move also closed three separate findings: the test can now call
the real resolver instead of re-implementing its lookup (a copy would have kept
passing if the metadata key were renamed — the exact drift this ticket is
about), and the two modules can no longer disagree about where a phrase lives.

**"Askable" had two definitions and they were the same set only by accident.**
`ask_for_fields` built the model's closed enum from `__dataclass_fields__`; the
guard checked `dataclasses.fields()`. Those differ on pseudo-fields — annotate
`_RULES` as a `ClassVar` and the tool would have offered the model `_RULES` as
a field to ask about while the guard skipped it. `askable_fields` in the domain
is now the one definition and both read it.

**Three more holes review found in the guard itself.** It only recorded
`_RULES` keys beginning with an underscore, so a sentinel named without one, or
a rule placed on a `MODEL_AUTHORED` field, reached the resolver with no test to
prevent it. It keyed by field name across classes, so a name shared by two
types could hide a missing phrase behind a present one. And it asserted the
*subjects* and not the *phrases*, so a phrase could be reworded silently — the
one thing this ticket most needed to be impossible, since the whole change is
moving those strings. All three closed; the sixth mutation run confirms a
reworded phrase now fails.

**`OneOf.ask` is genuinely required now.** It was defaulted to `""` and
enforced in `__post_init__`, while the docstring claimed "required rather than
defaulted" — a docstring contradicting the code it sits on, in a ticket about
prose drifting from code. Omitting it is the constructor's `TypeError`; a blank
one still gets the message that says why a rule has nowhere to fall back to.

**CLAUDE.md was wrong before this ticket and my first correction was wrong
too.** It said "four of the seven". Ticket 13 makes it eight, and my first
edit claimed both numbers were right when written. They were not: `project`
was already being asked, through the fallback, so the system asked eight
questions while the file said seven. The paragraph that warns about
documentation drifting from code had drifted, by one, and the fallback is why
nobody saw it. Stated plainly now, and the count is asserted rather than only
written — `test_which_questions_this_rule_binds_is_derived_not_counted`.

That test also moved: the split belongs beside the rule that needs it, in
`tests/test_responder_check.py` where `_KEPT` lives, not in the validation
tests. Asserting one module's invariant in another module's test file was mild
Divergent Change and review flagged it.

**One sentence I should not have deleted.** The first edit removed "A test says
this out loud, so the paragraph cannot quietly become a stronger promise than
the code makes" — still true, its test still exists. Both reviews caught it
independently. Restored.

**Prose was in six places and is now in one.** The reversal argument had been
copied into CLAUDE.md three times, `models.py`, `OneOf`, the resolver and two
test docstrings, so the next reversal would have been six edits. `OneOf` holds
it; everything else points there.

**Questions verified by rendering, not by reading the diff.** Seven read
byte-identically to what `_ASKED_AS` produced; `project` changes from "the
project" to "which project you need access to", which is the whole point.

**Six guards, each deleted once and watched go red:** `project`'s phrase, the
sentinel's phrase, the resolver's refusal, the blank-`ask` refusal, the askable
filter, and a silently reworded phrase. Green on restore. The fourth needed a
test written for it — it passed the first mutation run with the guard deleted,
which is the second time on this board that mutation testing found a guard I
believed was covered.

**Suite: 947 passed, 1 skipped, 0 failed.**
