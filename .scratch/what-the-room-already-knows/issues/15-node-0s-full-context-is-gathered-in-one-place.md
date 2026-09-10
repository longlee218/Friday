# 15: Node 0's full context is gathered in one place

**What to build:** Everything the extractor is shown about a task is gathered
by one function, returned as one value, threaded down as one object, and
rendered from that object alone. Today the full build is split across two
places that do not know about each other: node 0 fetches the transcript —
with its budget, its cooldown and its ineffective-compaction bookkeeping —
and the extractor's own `would_ask` fetches the room, the domain memories
and the open questions. Ticket 08 added `known` and had to thread it through
five signatures to get it from the first place to the second. That is the
shape D26 exists to end: one gather, one value, and a single point at which
"what did this task's extraction see, and why" can be logged and inspected
before it becomes a prompt.

Follows 14 by convention rather than by dependency: the module placement,
the value shape, the log line and the guards are decided there on the
smaller family, and this ticket applies them to the larger one.

**Blocked by:** 14

**Decisions:** D2, D3, D4, D5, D6, D7, D8, D26

**Status:** done

- [x] A `FullContext` value exists — `transcript` (what the reporter has said,
      already under budget, or nothing), `room`, `domain_memories`, `asked`
      (the questions this task has asked and not had answered), `known` (the
      task's parameters as they stand) — frozen, carrying nothing else, and
      named `transcript` rather than `text` so it cannot be mistaken for one
      message's body
- [x] One function, `build_full_context`, beside the extraction family's
      prompt module, returns it; node 0 calls it once per pass and is the
      only caller in production
- [x] **The builder reads and never writes.** It takes the budget, consults
      the cooldown itself (a read), fetches the transcript under the
      effective budget, and reports on the value whether the transcript is
      still over budget. Recording an ineffective compaction — the one write
      on this path — stays in node 0, decided from that field. This is what
      lets a fingerprint, a test or a probe call the builder with no side
      effect, and it is asserted: a test calls the builder against an
      over-budget task and checks nothing was recorded
- [x] One object travels the whole path: node 0's `prepare` takes the
      `FullContext` where it took `text`; the `extract` callable — both the
      remembering wrapper and the family's own — takes it; `Extractor.run`
      and `would_ask` take it; `build_input` takes it and nothing else. The
      five-signature `known` threading from ticket 08 collapses into one
      field of one object, and the ticket says so where that threading was
      documented
- [x] `input_fingerprint` hashes what `build_input` renders from the same
      object node 0 will hand to the model — the fingerprint and the call can
      no longer be built from two separate gathers, which is a stronger
      version of the guarantee ticket 01 established
- [x] Every guard ticket 08 added still holds through the object: the budget
      truncates oldest-first and keeps the newest, the count cap still binds
      under a generous budget, two ineffective compactions stop a third, the
      cooldown gate skips the check, a filled field drops out of the schema,
      an empty string does not count as filled, and `known` still moves the
      fingerprint. Each is re-run as a mutation on the new path, not assumed
      to have survived
- [x] **Contract:** the extractor no longer holds a store or a context store
      of its own — both reach the builder from node 0's own dependencies,
      where the context store already travels. The registration function
      loses those two parameters and the composition root stops passing
      them. Nothing else in the extractor changes: the harness, the params
      class, the parse, the hygiene, the clarification capture are untouched
- [x] The docstring on `Extractor.run` that argued for threading a string
      rather than a dict "through four layers" is reversed in the same
      commit and says why: the object is the point, and it is one object,
      not a dict
- [x] The prompt is byte-identical before and after for every case the
      existing suite exercises — a room fact, an open question, a domain
      memory, a filled field, a budget-truncated transcript — asserted by
      building both ways and comparing for equality, not by re-running the
      classifier evaluation, which this ticket does not touch
- [x] One log line per build, at debug level, as in 14: transcript length
      in characters and estimated tokens, whether it is over budget, whether
      a room was present, how many domain memories, how many open questions,
      which fields are already known — counts, sizes and field *names*, never
      a value or a message body
- [x] The list of gather modules written in 14 gains its second entry — the
      literal list, not a glob, not a derivation from the family names — and
      the same `ast` rule holds for this module: no section-builder import,
      no section, no join
- [x] CLAUDE.md is corrected in the same commit: the ticket-08 paragraph
      that describes `known` travelling five signatures, the ticket-01
      paragraph on where the room is looked up, the layout table's
      `friday/extraction/` row, and the `ExtractionMark` note on what the
      fingerprint covers
- [x] **DAG-ready, not DAG-resident — deliberately.** `FullContext` is the
      value story 44 wants a later node to read from the run's own state,
      and this ticket shapes it for that (frozen, data only, no store
      handle) — but does **not** write it into `DAGState`. No second node
      exists to read it, and a value in state with no reader is the
      seam-without-a-consumer D2 forbids. The next piece of DAG work adds
      the line in node 0 that puts it into state *and* the node that reads
      it, together; D26's closing paragraph records the two constraints
      that ticket inherits. This ticket's Comments say so, so the boundary
      is a decision on record rather than an omission
- [x] Guards deleted once and watched go red: the builder being bypassed for
      any one of its five inputs, the read-only rule (a write inside the
      builder), the fingerprint being computed from a second gather, the
      byte-identity test, the `ast` rule, the module list, and the log line

## Comments

`FullContext`, `build_full_context` (`friday/extraction/context.py`),
`build_input` and `Extractor`/`extract`/`input_fingerprint`/`register`/
`register_extractors` (`friday/extraction/prompt.py`,
`friday/extraction/__init__.py`) all now take one object instead of the
five-parameter thread ticket 08 left behind. `prepare_node`/`_prepare`/
`prepare`/`_remembering` (`friday/dag/prepare.py`) build it once per pass and
hand it down; `build_simple_dag`/`register_dags`/`run_agent.py` carry
`context_store` to node 0 the same way `budget_tokens` already travelled.

**A checklist line overstates its own premise, caught by review.** The
"CLAUDE.md is corrected" item names "the ticket-01 paragraph on where the
room is looked up" — no such paragraph exists in CLAUDE.md at any point
before this ticket; the extraction-side room lookup was never given its own
paragraph, only folded into the ticket-08 budget bullet's "known" material.
What this ticket actually did is add a new sentence there ("The room is
resolved here now, not by the extractor") describing the post-state, not
correct a stale one. The substance the checklist wanted is present; its
"corrected" framing is not literally true. Left as-is rather than rewriting
the checklist item after the fact, since the record of what was actually
found is more useful than a tidied premise.

**A sixth field, and why it earns its place.** The ticket names five:
`transcript`, `room`, `domain_memories`, `asked`, `known`. D6's cooldown
bookkeeping is a *write* (`record_ineffective_compaction`), and "the builder
reads and never writes" is one of this ticket's own criteria — so the
builder cannot make that write itself, but node 0 still has to know whether
to. `transcript_over_budget: bool = False` is that signal: bookkeeping about
the fetch, not new content the model is shown, computed once by the builder
and read once by node 0. Recorded as a judgement call rather than folded in
silently, since the ticket's own checklist enumerates five and this is a
sixth.

**A real bug, found while wiring `context_store` through.** `register_dags`
was writing `DAG_DEPS_EXTRA["context_store"] = context_store`, but
`DAG_DEPS_EXTRA: dict[str, dict[str, Any]]` is read by `pool.py` as
`DAG_DEPS_EXTRA.get(task.type, {})` — keyed by task *type* ("api_issue"
etc.), never by the literal string `"context_store"`. Grepping for read
sites of that key turned up none: the value had never once been reachable
since it was added. Fixed by threading `context_store` through the same
closure pattern `budget_tokens` already uses (`build_simple_dag` →
`prepare_node` → the `_prepare` closure), and removing the dead write.
`register_dags`'s own docstring now explains why.

**A pre-existing test-double gap, preserved rather than "fixed".**
`tests/test_dag_prepare.py`'s `_StandsForAnExtractor` double never threaded
`known` into what it rendered, even after ticket 08 made `known` a real
input to `build_input` — so `test_a_parameter_change_the_extractor_cannot_
see_is_not_paid_for` and `test_a_question_the_extractor_raised_survives_
the_skipped_call` never actually exercised known-based schema filtering,
despite both existing on a codebase where the real `Extractor` did filter
on it. This surfaced during migration: giving the base double the real
`context.known` (matching what `build_input` actually does now) broke both
tests, because a param change the double could not "see" started moving its
own rendered prompt. The base double now renders with `known` forced to
that class's own blank instance — reproducing the old, arguably-incomplete
double faithfully — and the two call sites that *do* need real known-based
filtering (`_RecordsWhatItWasShown`, `_UsesKnown`) override `would_ask` to
render the object unmodified. A mechanical migration's job is to preserve
existing behaviour, not to quietly repair a stub that undertested it; noted
here rather than silently changed.

**Classifier evaluation not owed.** CLAUDE.md's verifying-a-change rule
requires `evals/run_triage_eval.py` for a change to `friday/triage/prompt
.py` or anything upstream of it. This ticket never touches triage — its own
`friday/triage/context.py` and `LightContext` are untouched — so the
byte-identity test (`tests/test_extraction_context.py`'s `test_the_prompt_
is_byte_identical_gathered_or_assembled_by_hand`) is what stands in for
"did extraction's own rendering change", the same role it played for ticket
14 on triage's side.

**Test migration, mechanically.** `tests/test_extraction.py`'s four tests
that exercised `Extractor`'s own (now-removed) room/memory/question lookup
moved to a new `tests/test_extraction_context.py`, rewritten against
`build_full_context` directly — the seam those reads live at now — following
the same file-placement precedent ticket 14 set (`build_light_context`'s
own tests stayed in `tests/test_triage.py` rather than a separate file;
here a *new* file made sense instead, since `build_full_context`'s reads
span four different stores and the existing `test_extraction.py` was
already 670-plus lines before this ticket). `tests/test_dag_prepare.py`'s
stub extractor classes and every `prepare()`/`would_ask()`/`run()` call site
were updated to the new signatures; `tests/test_prompt_sections.py` and
`tests/test_artifacts.py` needed the same `_context(...)`-wrapping at their
few `build_input`/`ext.run` call sites. Full suite green throughout
(1076 passed, 1 skipped at the end).

**Mutation sweep**, one at a time, synchronous, backups in
`/tmp/t15-backups/`: bypassing the transcript, the room, the domain
memories, and `asked` gathers each turned red across `tests/test_dag_
prepare.py` and `tests/test_extraction_context.py`; neutralising `known`'s
effect on the rendered schema turned four tests red across three files;
adding a write inside the builder turned the new "builder never writes"
test red; swapping the render order in `build_input` turned the byte-
identity test and an existing ordering test red; adding a forbidden import
to `friday/extraction/context.py` turned the `ast` guard red; shrinking the
literal module list back to one entry turned both of `tests/test_context_
builders.py`'s own assertions red; and widening the debug log line to
include the transcript/asked content turned the never-carries-content test
red. Every mutation restored and the full suite re-confirmed green
afterward.
