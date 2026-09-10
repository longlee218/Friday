# 14: Triage's light context is gathered in one place

**What to build:** Everything triage is shown about a mention — this person's
turn and the room's summary — is gathered by one function, returned as one
value, and rendered from that value alone. Today `Triage.decide` looks the
room up itself and hands the turn and the room to `build_input` as two
separate arguments; there is no single point at which "what did the
classifier see for this mention" exists as a thing that can be logged or
inspected before it becomes a prompt.

This is the smaller of the two families and goes first on purpose: it settles
the shape — where the gather function lives, what it returns, what it logs,
what guards it — on a build with two inputs, so the full build in 15 follows a
convention rather than inventing one.

The prompt does not change. Not one byte: the same turn and the same room
reach the same section builders in the same order, and a test says so by
building the prompt both ways. That is what makes this a refactor rather than
a change to the classifier, and why the evaluation is not owed.

**Blocked by:** 09 (done)

**Decisions:** D2, D3, D4, D26

**Status:** done

- [x] A `LightContext` value exists — `turn` (the messages of this person's
      turn, oldest first, as `TriageRunner` already computes it) and `room`
      (the channel's context, or nothing) — frozen, and carrying nothing else
- [x] One function, `build_light_context`, beside triage's prompt module,
      returns it; it takes the context store, the channel, and the turn, and
      it is the only place triage resolves a room
- [x] Computing the turn stays in `TriageRunner` and out of the builder,
      deliberately: the runner needs the turn for its own "is this turn
      closed" decision, not only for the prompt, and a builder that
      recomputed it would be a second place deciding what a turn is. The
      ticket records this boundary rather than leaving it to be rediscovered
- [x] Triage's `build_input` takes a `LightContext` and nothing else — no
      `room=` keyword, no separate turn argument — and every caller in the
      suite and in the evaluation harness is migrated to build one
- [x] `Triage.decide` calls the builder once and renders from the value; it
      no longer touches the context store directly
- [x] The prompt is byte-identical before and after: a test builds the same
      mention's input the old way (turn plus room, as the previous
      `build_input` composed them) and the new way and asserts equality —
      not "contains the same words", equality — so the classifier's
      evaluation numbers from 09 still stand without a run
- [x] One log line per build, at debug level, saying what was gathered as
      counts and sizes — how many messages in the turn, how many characters,
      whether a room summary was present — and never any content: a message
      body or a summary field in a log is a second, unredacted renderer of
      what the store already records in `model_calls`
- [x] An `ast` test asserts the builder's module imports nothing from the
      section-builder module, constructs no section and joins nothing — D4's
      rule, stricter than the one prompt modules are held to, since a prompt
      module may import builders and this one may not
- [x] The test that lists gather modules is written here, with one entry,
      asserted as a literal list rather than derived from the family names;
      15 adds the second entry to the same list
- [x] CLAUDE.md's ticket-09 paragraph is corrected in the same commit where
      it describes how triage's input is assembled, and the layout table's
      `friday/triage/` row names the new module
- [x] Guards deleted once and watched go red: the builder being bypassed (a
      room resolved somewhere other than the builder), the byte-identity
      test, the `ast` rule, and the log line

## Comments

**The evaluation harness needed no migration.** `evals/run_triage_eval.py`
calls `Triage.decide(...)` at the `Triage` level, not `build_input`
directly — it never touched the two-argument shape this ticket replaced, so
"migrated" turned out to mean two files: `tests/test_triage.py` and
`tests/test_prompt_sections.py`, the only two that imported
`friday.triage.prompt.build_input` by name.

**The list-literal guard needed a second try.** The first version of
`test_the_list_of_gather_modules_is_not_derived` asserted the *result* —
`[p.name for p in _context_modules()] == ["context.py"]` — which a
`root.glob("*/context.py")` satisfies exactly as well as the literal list
does while there is only one gather module in the whole tree. Caught by
running the mutation this ticket's own checklist asks for: swapping the
literal for a glob left the test green. Fixed by inspecting
`_context_modules`'s own source with `ast` for a call to `glob`, `rglob`,
`iterdir`, `walk`, `scandir` or `listdir` — a property of *how the list is
built*, not of what it currently contains, which is the only way to fail
before a second gather module exists to disagree with a derivation.

**Two CLAUDE.md sentences tripped the doc-path hygiene test.**
`` `friday/triage/context.py::build_light_context` `` read as a path
including `::build_light_context` to `test_doc_paths_resolve_to_existing_files`,
which does not exist; reworded to `` `friday/triage/context.py`'s
`build_light_context` ``, two backtick spans instead of one. A sentence
naming `friday/extraction/context.py` — ticket 15's module, not yet
written — is fixed by not backticking a path that is deliberately not
there yet, the same convention `docs/adr/` already uses elsewhere in this
file for the same reason.

**The classifier evaluation.** Not run, per the ticket's own opening
paragraph: the prompt is byte-identical, proven by
`test_the_prompt_is_byte_identical_gathered_or_assembled_by_hand` comparing
the old two-call formula against the new one-value call for equality, not
similarity. Same judgement as tickets 01, 07, 08 and 10.

**Guards, deleted once and watched go red, synchronously — five of them:**
`Triage.decide` bypassing the builder (reverted to inline room resolution),
`build_input` dropping the `channel_derived` section, the gather module
importing from the section-builder seam, the debug log line being removed,
and the module-list guard itself (the glob mutation above). Each caught its
own test and nothing else, restored and diffed against a pre-mutation
backup before the next.

**Suite: 1074 passed, 1 skipped** (up from 1066 before this ticket — 8 new
tests, in `tests/test_triage.py` and a new `tests/test_context_builders.py`).

## Review

Two-axis review against `0172129` (ticket 08's commit). Both axes came back
clean — no hard violations, no missing checklist item, no scope creep. The
fallback (`list(turn) or [event]`) was independently confirmed to still sit
in `Triage.decide` and not inside the builder, matching the boundary this
ticket records. The byte-identity test was independently confirmed to call
the exact same `assemble`/`channel_derived`/`conversation` shape
`build_input` executes, not a hand-typed formula that happens to agree.

Spec review flagged one item as a "minor gap": the debug log line's
content-shape ("never a message body or a summary field") having no
standing test. It does —
`test_build_light_context_logs_counts_and_sizes_never_content` in
`tests/test_triage.py`, which seeds a store whose room summary and turn
both carry a distinctive marker string and asserts neither reaches the
captured log record. It is also one of the five guards this ticket's own
mutation sweep exercised (removing the log line entirely turned it red).
The reviewing agent's scan missed it — `test_triage.py` is a large file —
and rechecking it directly rather than accepting the report at face value
is why this line exists. No code change made; the checklist item already
had a real, mutation-verified test.

Full suite re-run: unchanged, 1074 passed, 1 skipped.
