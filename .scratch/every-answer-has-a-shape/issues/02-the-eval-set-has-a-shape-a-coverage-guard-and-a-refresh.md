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

**Status:** ready-for-agent

- [ ] A test over the frozen file fails when any member of the closed decision set has no row.
- [ ] The builder runs against a live database, takes whatever confirmed classifications exist, and prefers them over a seed row saying the same thing.
- [ ] A row carrying a multi-message turn still round-trips through the file unchanged.
- [ ] Anything the live prompt shows as a few-shot example is still excluded, and a test proves the exclusion excludes.
- [ ] `evals/README.md` says what the set must cover and what filling it involves.
- [ ] All of it is unit-tested without the network.
