# 03: Fill the set and baseline today's classifier against it

**What to build:** the measurement that every later ticket is judged against.
The rows are the operator's to write — a classifier scored against labels a model
chose is measuring nothing (D18) — so this ticket is gated on them. Once the file
is filled and frozen, the **current** classifier is scored against it and the
accuracy, confusion matrix and threshold table are written down. D16 is the whole
point of the ordering: rebuild the ruler first, read today's code with it, then
change the code. Rebuilding and changing at once moves both and the difference
means nothing.

**Blocked by:** 02.

**Status:** ready-for-human

- [ ] The frozen set holds rows at the boundaries between task types, rows a correct classifier should call `skip`, and rows whose turn is several messages.
- [ ] Rows read like the channel does — Vietnamese, a pasted `curl`, a correlationId, a stack trace — rather than like an English description of it.
- [ ] The coverage guard from ticket 02 passes against the filled file.
- [ ] The current classifier is scored against it and the numbers are recorded where the next person can find them.
- [ ] The cost of that run is written down beside it.

## Comments

**Not done — waiting on the operator.** The tooling, the shape, the coverage
guard and the refresh are in place (ticket 02) and `evals/README.md` says what
the rows have to cover. Filling them in is a data change to
`evals/triage.jsonl`; D18 is explicit that the labels have to be somebody’s
judgement, and a classifier scored against labels a model chose measures
nothing.

Once the rows are in: `uv run python -m evals.run_triage_eval` against the
current code, and record accuracy, the confusion matrix, the threshold table
and what the run cost. That reading is the baseline ticket 07 is judged
against, and D16 is the reason it has to be taken before triage changes rather
than after.
