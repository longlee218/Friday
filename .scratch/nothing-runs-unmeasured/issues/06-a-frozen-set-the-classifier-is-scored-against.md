# 06: A frozen set the classifier is scored against

**What to build:** `evals/`: a committed set of real messages with the labels
the operator marked, and a runner that scores the live classifier against it.

**Blocked by:** None (can start immediately)

**Decisions:** D6, D7

**Status:** done

## Why

`docs/DESIGN.md`, under Repo conventions: *"Triage gets a fixture set of real
messages with expected labels — the regression net for prompt changes."* It was
never built. The suite drives a scripted transport, which pins wiring and says
nothing about whether the classifier is right.

What that costs is visible on the other board. Ticket 06 there changed the text
of the highest-volume prompt in the system, and the only evidence that
classification survived was a probe written by hand over six messages, run
once, with the results pasted into the ticket and the script thrown away. That
is the best available answer today and it is not one that can be repeated.

`confidence_threshold: 0.7` carries a comment saying it is a placeholder and
"do not trust this number yet". There is no way to move off a placeholder
without a scored set.

The labels already exist. `db.confirmed_classifications()` returns
classifications the operator marked ✅, and today they are used only as
few-shot examples — the same data teaching the classifier and never grading it,
which is how drift gets a floor of zero.

Frozen, not queried (D7): an eval set that re-reads the database moves under
the prompt it is scoring, and a message used as a few-shot example must not
also be scored.

## Acceptance criteria

- [x] `evals/triage.jsonl`, committed, built once from marked verdicts plus the
      hand-written ones in `config.yaml`, with examples used in the prompt
      excluded from it
- [x] A runner outside `pytest` that calls the configured provider and prints
      accuracy, a confusion matrix, and what each threshold value between 0.5
      and 0.9 would have escalated
- [x] It reads the same prompt assembly production reads — not a copy
- [x] Documented in `CLAUDE.md` under Verifying a change: a prompt edit is not
      done until this has been run and the numbers reported
- [x] Its cost per run is stated in the README of `evals/`

## What it came to

**The real database has zero marked verdicts.** `db.confirmed_classifications()`
against `data/friday.db` returns nothing — nobody has reacted ✅ to a
classification yet — and `config.yaml`'s `triage_examples` is also `[]`. So
"built once from marked verdicts plus the hand-written ones in `config.yaml`"
had nothing to build from. `evals/build_triage_set.py`'s `SEED` — sixteen
hand-written rows, four per classifiable type, bilingual because real reports
are (`CLAUDE.md` already cites "token hết hạn rồi" as an ordinary one) — is
what `evals/triage.jsonl` is entirely made of today, documented as such in
both the seed's own docstring and `evals/README.md` rather than left for a
reader to discover. Delete a seed row once a real marked verdict says the
same thing.

**Two pure modules carry the arithmetic and the set-building, both
unit-tested with no network and no database**: `evals/scoring.py`
(`accuracy`, `confusion_matrix`, `threshold_table`) and `evals/dataset.py`
(`build_frozen_set`, `load_jsonl`, `write_jsonl`). `build_frozen_set` is D7 in
code: it excludes anything also in the prompt's few-shot examples, because
scoring a classifier on the sentence it was already told the answer to is not
a measurement of anything.

**`evals/run_triage_eval.py` is the runner.** It builds a real
`friday.triage.Triage` through `friday.triage.runner.build_triage` — see
below — reads `evals/triage.jsonl`, calls `triage.decide()` once per row
against the configured provider, and prints accuracy, a confusion matrix
(every label gets a row and a column even at zero, so a type the classifier
never once guesses does not vanish from the table), and the threshold
table. Run for real against MiniMax-M3 on the shipped 16-row seed set:
93.8% accuracy, one `doc_question` example landed as `needs_human` instead.
Measured cost for that run: ~15,000 input tokens, ~1,100 output tokens —
`evals/README.md` states tokens rather than a dollar figure, since the
provider `config.yaml` points at decides the price, not this tool.

**Every guard was mutation-tested**: the few-shot exclusion, the dedup, the
`_to_prediction` branch, `confusion_matrix`'s label union, and — caught only
because a first mutation attempt slipped through silently — a boundary test
for `threshold_table` at `confidence == threshold` exactly, since `<` and
`<=` agree everywhere else and only that boundary tells them apart (it must
be `<`, matching `TriageRunner._apply`'s own `>=`).

## What the review changed

One review pass, scoped to this ticket alone (unlike 08/09, no shared
working tree with another ticket). One real finding, not blocking but acted
on:

**`evals/run_triage_eval.py`'s `_build_triage` duplicated ~15 lines of
`TriageRunner.build`** — fetching `confirmed_classifications`, reading the
`examples` setting, constructing `Sensitive` — rather than calling it. Not
wrong today (checked line-by-line against production), but a drift surface:
if `TriageRunner.build` later changes what feeds the prompt, the eval's copy
would not follow, and the numbers this tool prints would silently stop
representing the live classifier — the reviewer named this as "not blocking,
but flag it", and it is exactly the class of finding ticket 09's review
found repeatedly (`MemoryScope` behind a quoted forward reference, a bare `8`
standing in for `RESULTS`) and always acted on rather than left standing.

Split the shared part into `friday.triage.runner.build_triage(config, *, db,
record=None, spent=None) -> Triage` — the one place `Triage` is now
assembled from configuration and a store. `TriageRunner.build` calls it;
`evals/run_triage_eval.py`'s `_build_triage` is now four lines: open the
database `FRIDAY_DB` or `config.database_path` names, call `build_triage`,
close it. Three new tests (`tests/test_build_triage.py`) pin the function
directly — wired examples, the configured example count reaching the store
call, and the `SystemExit` (not a bare `KeyError`) for a missing `triage`
agent block — each mutation-tested and confirmed to go red. Re-ran the real
eval against MiniMax-M3 after the refactor: same 93.8% accuracy, same
confusion matrix, confirming the refactor changed nothing about what
production actually does.

The reviewer's other finding (#0, `poke.py` showing as deleted in `git
status`) is unrelated to this ticket — a pre-existing, already-flagged
collateral change from a concurrent session, left untouched per explicit
standing instruction.

779 tests pass (774 before this ticket's own work started). mypy: 31,
unchanged.
