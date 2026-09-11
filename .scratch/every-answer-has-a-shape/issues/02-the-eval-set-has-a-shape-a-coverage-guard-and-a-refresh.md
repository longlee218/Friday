# 02: The eval set has a shape, a coverage guard, and a refresh from real verdicts

**What to build:** the room for the rows the operator will write (D18), and the
guard that stops the set quietly losing them again. Every member of the closed
decision set — `skip` included — must be represented in the frozen file, and a
test over the file says so, because a value nothing is scored against is a value
nothing protects (D17). The builder refreshes from classifications the operator
has actually marked ✅ and keeps the seed only where nothing real yet says the
same thing (D19); the few-shot exclusion rule is unchanged and still load-bearing.

This ticket adds no rows of its own beyond what coverage strictly requires: a row
invented here to make a number look healthy would be the measurement marking its
own homework.

**Blocked by:** None (can start immediately).

**Status:** done

- [x] A test over the frozen file fails when any member of the closed decision set has no row.
- [x] The builder runs against a live database, takes whatever confirmed classifications exist, and prefers them over a seed row saying the same thing.
- [x] A row carrying a multi-message turn still round-trips through the file unchanged.
- [x] Anything the live prompt shows as a few-shot example is still excluded, and a test proves the exclusion excludes.
- [x] `evals/README.md` says what the set must cover and what filling it involves.
- [x] All of it is unit-tested without the network.

## Comments

Three things, and a prefactor that turned out to be the largest of them.

**The prefactor.** The closed decision set was written down three times in
three shapes that could disagree: a `TaskType` literal in `friday/domain/models.py`
that no annotation ever used, a `CLASSIFIABLE` tuple in the store, and the
`classify`/`skip` tool pair that only adds up to the set if you know about
both. `DECISIONS = (*PARAMS, SKIP)` replaces all three. The eval guard needed a
source, and writing `set(PARAMS) | {"skip"}` into a test would have made four.

**`unfit(examples)`** returns the reasons a set is not fit to score against, in
sentences, and is run both by the suite over the committed file and by the
refresh over what it just produced. It checks only what code can decide — a
decision with no row, a set with no multi-message turn, a duplicated text. The
three D17 asks for that are judgements about content (boundary rows, `skip`
rows, rows that read like the channel) are stated in `evals/README.md` as the
operator’s, because a check that guessed at them would either pass everything
or refuse rows the operator meant.

**D19 read backwards and nobody could have noticed.** `build_frozen_set`
concatenated `confirmed` then `seed` into one dict keyed by text, so the seed
won every collision — the opposite of "the seed is kept only where nothing real
yet says the same thing". Invisible in the case that made anyone write both,
since there the two agree on the label.

Six guards mutated one at a time, including stripping every `skip` row out of
`evals/triage.jsonl` to watch the file guard fire.
