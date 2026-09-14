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

**The baseline is taken at `bee022f`, not at `HEAD`, and that is a correction
rather than a detail.** This ticket said "against the current code", which was
true when it was written and stopped being true when ticket 07 shipped ahead of
it — 07 declared itself blocked by this one and was done anyway. Followed today,
that instruction would record an *after* as the baseline, which is exactly the
worthless measurement D16 exists to prevent. Found by a review of the whole
board, not by anyone reading the ticket.

Recovery is still open because `evals/triage.jsonl` has not changed since the
board began: the file at `HEAD` is byte-identical to the file at `b61a7e1`. So
the rows go in on this branch, and then:

    git stash                                     # keep the filled rows
    git checkout bee022f                          # the last commit before 07
    git stash pop                                 # the set, against old code
    uv run python -m evals.run_triage_eval        # the BEFORE reading
    git checkout main && git stash pop            # back, rows intact
    uv run python -m evals.run_triage_eval        # the AFTER reading

Record accuracy, the confusion matrix, the threshold table, the count of
decisions outside the closed set, and what each run cost. Two readings of one
ruler is what D16 asks for; one reading of a ruler that moved is what it
forbids.

**A caveat on the before reading, stated rather than discovered:** `bee022f`
has no out-of-set number, because the concept did not exist yet. Its rows land
as `needs_human` there and as `needs_human` plus a count here, so accuracy is
comparable and that one line is new rather than changed.
