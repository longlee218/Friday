# 10: Memory carries a kind and a lifecycle

**What to build:** A memory says what kind of thing it is and whether it is
still current. Today it is free text with a soft delete, so a decision that
changed sits beside the one it replaced with nothing to say which is live, and
correcting a typo is indistinguishable from a reversal.

The table has zero rows, so the migration has no blast radius.

**Blocked by:** 01

**Decisions:** D2, D13, D14, D16, D17

**Status:** done

- [x] A memory carries a kind, one of the five D14 names, and the reader follows
      from the kind rather than from a second field
- [x] A memory carries a status and a link to whatever replaced it
- [x] Correcting the wording of a memory is a different operation from replacing
      what it claims, and the two are named differently
- [x] Every reader that serves a model reads only active rows
- [x] The extractor reads the four domain kinds; the responder reads the voice
      kind and keeps its four tools
- [x] Voice material written by the responder's own tools lands in the table
      under the voice kind — the split between the two stores is by who writes,
      not by kind (D13)
- [x] The board shows what a superseded memory used to say and when it changed
- [x] One migration; existing rows take the kind matching their reader today and
      active status
- [x] Guards deleted once and watched go red

## Comments

**Mechanical shape.** `friday/domain/models.py` gains `MemoryKind` (five
values), `MemoryStatus` (`active`/`superseded`), `DOMAIN_KINDS`, and
`reader_for(kind)` — a function, not a stored column, so nothing has to keep
two fields in agreement (D14). `Memory` gains `kind`, `status`,
`superseded_by`. `friday/store/schema.py` mirrors the columns as plain
strings, the way `Task.state` already stores `TaskState`. One migration
(`385fddf60289`) adds all three with `server_default`s (`voice`/`active`) so
any database that already holds rows — the real deployment has none —
backfills to the kind matching its only possible writer today, which is the
responder.

**Two operations, named differently (D16).** `memory_update` is unchanged:
same claim, reworded, in place. `memory_supersede` is new: it marks the old
row `superseded` (pointing `superseded_by` at the replacement) and writes a
fresh active row under the same kind, so a reader that already trusts that
kind's shape keeps trusting it. `_live_memory` — the lookup `memory_update`,
`memory_supersede` and `memory_delete` all share — now also requires
`status == active`: a superseded row is frozen history, corrected or
retracted only through the row that replaced it, never itself. The cap
`memory_add` refuses against counts active rows only, so a long chain of
supersessions (many physical rows, one live claim) never makes a room look
fuller than it is — a chained-supersede test pins exactly this, and mutation
confirmed the count would otherwise miscount.

**Readers, split by kind.** `memory_search` — the responder's tool — takes a
required `kind` (no default, the same reasoning that made `limit` required:
a caller that forgot to say would otherwise agree with a default by
coincidence) and `friday/tools/memory.py` always passes
`MemoryKind.VOICE`; `memory_add` defaults to `MemoryKind.VOICE` instead,
because unlike `kind` on search, a default here names the one thing every
caller before this ticket already meant. `db.domain_memories(channel_id)` is
new: the four domain kinds, active, newest first — no query, because the
extractor has no memory tools of its own (D21, already decided by ticket 01)
and reads by injection. It shares `memory()`'s existing channel slot with
`room_facts` rather than claiming a third slot: both answer "what is this
room known to be", from two different producers, and a third block for the
same question is the split `memory()`'s own docstring already argues
against for `conversation`/`channel`. `Extractor.would_ask` fetches it the
same way it already fetches `unanswered_questions` — held db, per-call
`channel_id` — so `input_fingerprint`, which hashes `would_ask`'s own
output, picks up a changed domain memory automatically; ticket 01's node-0
double-billing bug (the fingerprint not knowing about a fourth input) cannot
recur here because there is nothing to remember.

**A scope decision, recorded rather than quietly made.** D21/D22 (the
injection mechanism, the memory section builder's caller) are ticket 01's
decisions, not this ticket's — ticket 10 only lists D2, D13, D14, D16, D17.
`memory_supersede` therefore ships with no agent-facing *tool* calling it:
the responder keeps exactly its four tools per the checklist, and no
producer of domain-kind memories exists yet (that is ticket 12's "candidate
memory" producer, D18–D20, explicitly out of scope here). The operation is
tested directly at the store, the way `memories_for_channel` already has a
caller in the board's API rather than in any agent — a real, working
capability with its consumer arriving in a later ticket, not a store built
without one. Recorded here so the choice is visible rather than assumed.

**The board.** `web/src/api-types.ts`'s `Memory` gains `kind`, `status`,
`superseded_by` — the API route already returned them via `asdict()` once
the domain dataclass carried them, so no `friday/ops/api.py` change was
needed. `RoomsScreen.tsx`'s `MemoryPanel` shows a memory's kind as a pill,
marks a superseded row struck-through the same way a deleted one already is,
and adds a line naming when it changed and what replaced it — reusing
`updated_at`, which `memory_supersede` sets on the old row at the moment of
replacement, rather than a new column for "when".

**The classifier evaluation.** Not run. `friday/agent/instruction_prompt.py`
is upstream of `friday/triage/prompt.py` in principle, but this ticket only
*adds* `remembered_facts` there — no function triage's prompt module
actually calls (`assemble`, `channel_derived`, `conversation`, `_one_line`,
`_render_pairs`) was touched. Triage's own source file is untouched. Same
judgement ticket 01 recorded for the same shape of question, and the same
reasoning: substantively unneeded because the classifier's prompt is
byte-identical, not skipped for cost alone.

**Guards, deleted once and watched go red, synchronously — nine of them:**
`reader_for`'s closed-set check, `_live_memory`'s active-status filter,
`memory_search`'s kind filter, `domain_memories`'s domain-kind filter,
`memory_add`'s active-only cap count, the tool's explicit `kind=VOICE` on
`memory_add`, `Extractor.would_ask` fetching domain memories at all,
`build_input` rendering them into `channel_body`, and `remembered_facts`'s
newline-flattening defence. Each one caught its own test and nothing else,
restored and diffed against a pre-mutation backup before the next.

**Suite: 1025 passed, 1 skipped** (up from 1002 before this ticket — 23 new
tests). `web/`'s `tsc --noEmit` and `npm run build` both clean.

## Review

Two-axis review against `ca96f47` (ticket 09's commit). Standards found no
hard violations and confirmed all five scrutiny points from the review brief
were sound as shipped (`memory_supersede`'s missing tool caller, the
`status` filter on `_live_memory`, the `kind`-default asymmetry, the
migration's `op.add_column` style, and CLAUDE.md's accuracy).

**Spec found one real defect: `reader_for` was decorative, not load-bearing.**
The checklist's own words are "the reader follows from the kind rather than
from a second field" — but `DOMAIN_KINDS` was a hand-written
`frozenset({FACT, CONSTRAINT, FINDING, DECISION})` sitting beside
`reader_for`, not derived from it. Nothing enforced they stayed in
agreement; a fifth kind added to `MemoryKind` without updating both would
have `reader_for` and `DOMAIN_KINDS` disagree about who reads it, which is
exactly the "two fields that have to agree" D14 exists to rule out. Fixed:
`DOMAIN_KINDS` is now `frozenset(k for k in MemoryKind if reader_for(k) ==
"extractor")` — the only place a kind's readership is decided is
`reader_for`, and `DOMAIN_KINDS` is one of its answers, not an independent
claim. Mutation-verified: reverting the derivation to a hand-listed subset
turned two tests red (`test_the_reader_of_a_memory_follows_from_its_kind`,
`test_domain_memories_reads_the_four_domain_kinds`), then restored.

Full suite re-run after the fix: 1025 passed, 1 skipped.
