# The classifier's regression net

`docs/DESIGN.md`, under Repo conventions: *"Triage gets a fixture set of real
messages with expected labels — the regression net for prompt changes."* This
is that, built for ticket 06 on `.scratch/nothing-runs-unmeasured/`.

## What is here

- **`triage.jsonl`** — the frozen set, one `{"text": ..., "expected": ...}`
  object per line. Frozen, not queried (D7): scoring never re-reads the
  database, so a prompt edit and a change in what the operator has since
  marked land in two different numbers, not one number nobody can attribute.
- **`dataset.py`** / **`scoring.py`** — pure functions, unit-tested under
  `tests/test_eval_dataset.py` and `tests/test_eval_scoring.py`. No network,
  no database.
- **`build_triage_set.py`** — refreshes `triage.jsonl` from live data. Run by
  hand, when there is new data worth freezing in:

  ```bash
  uv run python -m evals.build_triage_set
  FRIDAY_DB=/path/to/db uv run python -m evals.build_triage_set   # a different db
  ```

  Pulls `db.confirmed_classifications()` (verdicts the operator marked ✅)
  and a small hand-written `SEED`, and drops anything that is also in
  `config.yaml`'s `triage_examples` — an example already shown to the model
  as a few-shot cannot also be something the model is scored against.

  **As shipped, `triage.jsonl` is entirely `SEED`.** The real database has
  zero marked verdicts as of this ticket — nobody has reacted ✅ to a
  classification yet — so there is nothing real to freeze in. Delete a seed
  row once a real marked verdict says the same thing; the seed exists to
  give this tool something to score on day one, not to be defended forever.

- **`run_triage_eval.py`** — scores the live classifier against
  `triage.jsonl`:

  ```bash
  uv run python -m evals.run_triage_eval
  ```

  Builds a real `friday.triage.Triage`, the same way `TriageRunner.build`
  does — same few-shot examples, same `friday.triage.prompt` assembly — and
  calls the configured provider once per row. Prints accuracy, a confusion
  matrix, and how many rows each confidence threshold from 0.5 to 0.9 would
  escalate to a human, which is what `confidence_threshold` in `config.yaml`
  is checked against.

## What a run costs

Measured against the original 16-row seed set, on `MiniMax-M3` (the
`triage` agent's configured model): **~15,000 input tokens, ~1,100 output
tokens**, one call per row. Convert with your own provider's per-token
price — this file states the token count because that number does not
depend on which provider `config.yaml` points at; a dollar figure would.

`SEED` is 18 rows as of ticket 09 (two rows carrying a multi-message `turn`,
added to exercise what a single line cannot — see `CLAUDE.md`'s note on
triage's light context). The token figures above predate that change and were
not re-measured against the new set — `run_triage_eval.py` does not record
per-call cost (`_build_triage` passes no `record=`/`spent=`), so the number
above is the last one actually measured, not a guess scaled by row count.
Re-measure directly if the estimate needs to be precise; do not multiply the
old figure by 18/16 — the two new rows are each a multi-message turn, larger
than the average of the sixteen they were measured against.

Cost scales with the size of `triage.jsonl` and with how many few-shot
examples `config.yaml`'s `triage: examples:` setting shows the model (the
input side, since those are read once and included on every call) — not with
anything in `run_triage_eval.py` itself.

## When to run this

Per `CLAUDE.md`'s Verifying a change: a change to `friday/triage/prompt.py`
or to anything upstream of it is not done until this has been run and the
numbers — accuracy, the confusion matrix, and the threshold table — are
reported alongside the change. A probe written by hand once and thrown away
is what this replaced (see ticket 06 on `.scratch/nothing-runs-unmeasured/`
for why that was not repeatable).
