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

**Status:** done

- [x] The refusal is deterministic, involves no model, and sits at the single
      write path
- [x] It applies to every producer, the operator's own hand included
- [x] A table of lines that must be refused and a table that must be accepted,
      both asserted
- [x] The refusal is exercised through **every** producer, not one — a refusal
      one of three producers bypasses is what both prior arts shipped
- [x] A refused write says why, to whoever attempted it
- [x] The guard is deleted once and watched go red

## Comments

`friday/domain/memory_guard.py` is the new module: `check_not_instruction_
shaped(text)` raises `InstructionShaped` and is otherwise silent, and is the
single function every producer's write path calls before a line becomes a
row. Wired at five call sites: `Database.memory_add`, `memory_update` and
`memory_supersede` (`friday/store/db.py`), and `ContextStore.set_overrides`
and `init_channel` (`friday/memory/channel_context.py`).

**"Every producer, the operator's own hand included" reads as two different
producers, not three, once the code is actually read.** D19 names three:
the operator writing by hand, the operator confirming a candidate (ticket
12, not yet built), and the agent writing automatically (already wired,
`friday/tools/memory.py`). D19 is explicit that "the operator writing by
hand" *is* "the route that stores channel overrides" — `ContextStore.set_
overrides`/`init_channel` — not a second, not-yet-existing route onto the
`Memory` table. So this ticket's guard reaches two live producers today
(the overrides route and the agent's `memory_add`/`memory_update` tools)
plus `memory_supersede`, which has no caller yet but is a write path all the
same and will be ticket 12's second producer's route once it exists — put
under the guard now rather than left for that ticket to remember.

**The refusal is narrower than "any imperative sentence", and had to be.**
The first design read every directive-shaped line as refused — leads with a
bare verb, or with `always`/`never`/`don't` — and it broke a real, already-
shipped fixture the first time the suite ran: `tests/test_memory_store.py`'s
`test_domain_memories_reads_the_four_domain_kinds` stores `"never deploy on
fridays"` as a `MemoryKind.CONSTRAINT`, which is precisely the kind meant to
hold "what must not happen here, and what always has to"
(`friday/memory/channel_context.py`'s own summary job). Refusing every
directive-shaped line would have refused the one kind built to carry them.
The check is now the AND of two conditions — directive-shaped *and* naming
one of this system's own mechanism words (approval, validation, reply,
escalation, and so on) — which keeps `never deploy on fridays` and `must
include the X-Request-Id header` on the accepted side while still refusing
D25's own three examples (`send without approval`, `always reply in
English`, `skip the validation`). This is recorded here because it is a
real, deliberate narrowing of the ticket's own opening sentence ("a line
that reads as a directive cannot be stored"), not the literal rule as first
stated.

**Refused, not swallowed.** `InstructionShaped` propagates out of the store
and the context store; the tool layer (`friday/tools/memory.py`'s
`memory_add`/`memory_update`) and the API route
(`PUT /api/channels/{id}/context/overrides`, `friday/ops/api.py`) each catch
it explicitly and phrase the reason, rather than letting it fall through to
`harness._tool_failed`'s generic "that tool is unavailable right now" —
verified by mutation: removing either catch turns the model-facing message
back into that generic string with no reason in it.

**Mutation sweep**, one at a time, synchronous, backups in
`/tmp/t11-backups/`: neutralising the classifier's own AND condition,
bypassing the check in each of the three `db.py` write methods
individually, bypassing it in `set_overrides` and in `init_channel`
individually, removing the tool layer's catch on `memory_add` and on
`memory_update` individually, and removing the API route's catch — each one
turned a distinct test red, and every mutation was restored and the full
suite re-confirmed green (1088 passed, 1 skipped) afterward.

New file: `tests/test_memory_guard.py` — the refuse/accept tables the
ticket asks for, plus one test per producer (including the two currently
unused-by-a-tool store methods, `memory_supersede` and `init_channel`) and
one test proving the message layer, not just the raise.

**Review found a real bug in the first version of the mechanism check, and
it is fixed.** Both review axes independently caught it: `_names_this_
systems_mechanism` matched a raw substring, so `invoice` tripped on
`voice` and `resend` tripped on `send` — `never invoice a client twice` and
`must resend the confirmation email` were wrongly refused. Worse, `send`
and `reply` sit in both `_IMPERATIVE_VERBS` (shape) and `_MECHANISM_WORDS`
(meaning), so any sentence merely *starting* with one of those words was
refused regardless of what followed it — `send the invoice every month`
tripped for no reason connected to this system's own mechanism at all.
Fixed by matching whole words only (`_MECHANISM_PATTERN`, a compiled `\b…\b`
alternation) and by checking that pattern against the sentence *after* its
lead word, never the lead word itself — the two conditions now genuinely
have to be satisfied by different parts of the sentence. Six new lines
added to the accepted table pin the fix; verified the mutation (reverting
the remainder-only check) turns `test_the_named_facts_are_accepted` red.
Full suite re-confirmed green afterward (1088 passed, 1 skipped).

Review also flagged that `CONTEXT.md`'s `## Memory` section had no mention
of this guarantee — added one sentence there rather than a new heading,
since a refusal rule is a guarantee of the existing `Memory` concept, not a
new domain noun (the same reasoning that keeps `friday/domain/validation
.py`'s own rules off the glossary). The heading count CLAUDE.md's domain
docs section quotes (`grep -c "^## " CONTEXT.md`) is unchanged at 25.
