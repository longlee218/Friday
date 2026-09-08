# 11: An instruction-shaped memory is refused

**What to build:** A line that reads as a directive cannot be stored as a
memory. A memory is read back as a statement of fact by a run that has none of
the context that produced it, so "send without approval" or "skip the
validation" is an instruction with a long life and no author present — the most
dangerous row this board can create.

Both prior arts found this hazard and neither closed it: DeerFlow has a
deterministic gate whose own comment is that an instruction cannot be stored as
a memory, and never calls it from its add tool; Hermes asks the model in prose
to write declarative facts because imperative phrasing is re-read as a directive
in later sessions. A refusal at the write path is stronger than either.

Separate from 10 rather than folded into it, and safe to be: the table has zero
rows today, so no row can already violate the rule this ticket adds.

**Blocked by:** 10

**Decisions:** D19, D25

**Status:** ready-for-agent

- [ ] The refusal is deterministic, involves no model, and sits at the single
      write path
- [ ] It applies to every producer, the operator's own hand included
- [ ] A table of lines that must be refused and a table that must be accepted,
      both asserted
- [ ] The refusal is exercised through **every** producer, not one — a refusal
      one of three producers bypasses is what both prior arts shipped
- [ ] A refused write says why, to whoever attempted it
- [ ] The guard is deleted once and watched go red
