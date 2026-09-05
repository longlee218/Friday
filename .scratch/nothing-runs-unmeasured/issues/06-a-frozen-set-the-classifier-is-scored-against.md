# 06: A frozen set the classifier is scored against

**What to build:** `evals/`: a committed set of real messages with the labels
the operator marked, and a runner that scores the live classifier against it.

**Blocked by:** None (can start immediately)

**Decisions:** D6, D7

**Status:** todo

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

- [ ] `evals/triage.jsonl`, committed, built once from marked verdicts plus the
      hand-written ones in `config.yaml`, with examples used in the prompt
      excluded from it
- [ ] A runner outside `pytest` that calls the configured provider and prints
      accuracy, a confusion matrix, and what each threshold value between 0.5
      and 0.9 would have escalated
- [ ] It reads the same prompt assembly production reads — not a copy
- [ ] Documented in `CLAUDE.md` under Verifying a change: a prompt edit is not
      done until this has been run and the numbers reported
- [ ] Its cost per run is stated in the README of `evals/`
