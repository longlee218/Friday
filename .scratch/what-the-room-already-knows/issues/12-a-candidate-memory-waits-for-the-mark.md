# 12: A candidate memory waits for the operator's mark, and a full room says so

**What to build:** An agent can propose a memory, and nothing reads it back
until the operator marks it. This is producer ② — it restores the approval floor
that was given up when the staged-observation tier was retired, without
restoring the tier, which died for want of a producer and a place for a person
to look. This has both, and the gesture is the one that already confirms a
classification.

Second half: a room that has filled its memory says so to the one party who can
clear it. The ceiling refuses and evicts nothing, which is correct and stays —
but today the only party told is the model.

**Blocked by:** 10, 11

**Decisions:** D18, D19, D20

**Status:** done

- [x] A candidate is visible to the operator and is read by no prompt and no
      tool until it is marked
- [x] A candidate is never stored in a tier that a prompt reads — that is the
      tier that died, and this is not it
- [x] The mark is the gesture that already confirms a classification
- [x] Marked accepted, it is written with its kind, through the refusal in 11
- [x] Marked rejected, it is discarded visibly — an operator can see what was
      proposed and turned down
- [x] Silence is not a mark: an unmarked candidate stays unread indefinitely
- [x] A channel at its ceiling refuses the write, evicts nothing, and the
      condition appears on the board and in the heartbeat
- [x] Guards deleted once and watched go red

## Comments

Three design questions the ticket itself left implicit were put to the user
before writing any code, since each forks the implementation substantially:
who proposes (the responder, a new fifth tool `memory_propose`, alongside
`memory_add`); what the operator reacts to in order to resolve a candidate
(the task's own opening message — the same one `Verdict` is already keyed
on, per user story 31: "the gesture that confirms a memory [is] the gesture
that already confirms a classification... so there is one thing to learn");
and where a candidate lives (a new table, `memory_candidates`, not a status
on `memories`). All three: user's recommended option, confirmed.

**The mechanism, end to end.** `friday/domain/models.py` adds
`CandidateStatus` (`pending`/`accepted`/`rejected`) and `MemoryCandidate`.
`friday/store/schema.py` + migration `a423973bc96d` add the table.
`Database.propose_memory` stages one; `Database.resolve_candidates_for_
message` resolves every pending candidate tied to a message, called from
`run_agent.py`'s `marked()` right alongside `record_verdict` — the same
reaction event, not a second one. Accepting calls `memory_add` for real, so
ticket 11's refusal and the 200-cap both bind; either one failing leaves the
candidate `ACCEPTED` with `memory_id` still `None` rather than raising into
the live reaction handler (mutation-verified: removing that catch crashes
`test_an_instruction_shaped_candidate_is_accepted_but_not_written`).
`friday/tools/memory.py` gains `memory_propose`; `GET /api/channels/{id}
/candidates` is the operator's view.

**A race the ticket didn't name, closed anyway.** The operator marks
classifications on their own schedule — often *before* a later run proposes
anything against the same message, since marking is for the classifier's
few-shot examples and has nothing to do with when a task's memory tools
run. Without a check, a candidate proposed after the mark would wait
forever for a reaction event that already fired and will not fire again
(reactions only resolve on being *added*, never on being present). Closed
by `propose_memory` checking `verdict_for` on `scope.message_id` before
returning a `PENDING` row, and resolving immediately if one already exists.
`memory_propose`'s own tool answer reports the real outcome ("already
marked accepted") rather than the generic "waiting for a mark" when this
fires. Tested (`test_a_verdict_that_already_exists_resolves_a_proposal_
immediately`, and the `wrong`-mark twin).

**Half B, D18.** `Database.full_memory_channels` names every channel at the
cap; `Heartbeat.summary()` and `GET /api/board` (`full_memory_channels`,
also added to `web/src/api-types.ts`'s `Board` interface) both surface it.
The cap's own behaviour — refuse, evict nothing — is unchanged; only
visibility is new.

**Mechanically necessary, not a design change:** adding a fifth memory
tool broke every fixed-arity unpacking of `memory_tools()`'s return list
across `tests/test_tools.py` and `tests/test_memory_guard.py` (`search,
add, _, _ = ...` and friends), CLAUDE.md's several "four tools" mentions,
and `friday/agent/instruction_prompt.py`'s `MEMORY_TOOLS`/
`_MEMORY_TOOL_SYSTEM`. All updated; `test_the_memory_tools_named_in_the_
prompt_are_the_ones_declared` catches drift between the two automatically
since it iterates `MEMORY_TOOLS` rather than naming a count.

**`run_agent.py`'s own wiring has no direct unit test**, and that is
consistent with what was already true before this ticket: `marked()`'s
existing `record_verdict`/`clear_verdict` calls have never had one either
— `tests/test_verdicts.py` tests the *provider's* dispatch mechanism with
its own local stand-in, not this specific closure. `resolve_candidates_
for_message` itself is thoroughly tested at the store level
(`tests/test_memory_candidates.py`); the two-line addition inside
`marked()` was verified by reading, matches the existing `record_verdict`
call's exact shape, and is covered by `test_composition_root.py`'s
ast-based ordering guard (which still passes).

Ran `uv run mypy` against every changed file: introduced zero new errors
(confirmed by diffing against the pre-change baseline, itself already 13
pre-existing, unrelated errors) — one real gap found this way and fixed,
`_resolve_candidate` reading `session.get(...)` without a None-check.

**Review found one real polish issue, fixed.** `_resolve_candidate` logged
twice for the same event when `memory_add` raised `InstructionShaped`: its
own `except` block logged the specific reason, then the `if written is
None` check below logged a second, generic line claiming "the channel is
at its cap **or** the write was refused" — true as a disjunction, but wrong
about *which one* had just happened, for an event the code already knew
the answer to. Restructured so the cap case (a plain `None` return) and
the refusal case (a caught exception) each log exactly once, in their own
words.

Mutation sweep, one at a time, synchronous, backups in `/tmp/t12-backups/`:
the mark-vs-accept gate, the `InstructionShaped`/cap-refusal catch (twice,
each independently), `full_memory_channels`'s cap threshold and its
deleted/superseded filter, the heartbeat line, the board field (caught by
both the direct API test and the web-contract cross-check), and the tool's
immediate-resolution reporting. Every mutation restored and the full suite
re-confirmed green (1111 passed, 1 skipped) afterward.
