# 07: Triage answers one closed set, `skip` included

**What to build:** triage returns one validated decision — a member of a closed
set generated from the registry of task types plus `skip` — and a confidence
(D6). This **reverses** the recorded split between `classify` and `skip`, and the
reversal is the point: two tools meant two validations, and an invented type could
reach the runner and open a task the pool then discovers has no graph. One enum,
validated once, before anything is opened. What the old split protected — that
`skip` opens nothing — is the runner's business and stays there.

The evaluation runner follows the new shape, and owes one new number: a decision
outside the closed set is counted and reported separately, because "it invented a
type" and "it chose the wrong type" are two different failures and counting them
together would hide the one this change exists to make impossible (D20).

**Blocked by:** 03, 05.

**Status:** done (eval reading owed, gated on ticket 03)

- [x] A decision outside the closed set never opens a task.
- [x] `skip` is validated exactly as strictly as every other member, and still opens nothing.
- [x] Triage's answer is the return value of the call that asked for it; nothing is read off a capture.
- [x] The eval runner scores the new shape and reports out-of-set decisions as their own number.
- [ ] The classifier is scored against the frozen set and compared to ticket 03's baseline, with accuracy, confusion matrix and threshold table reported.
- [x] CLAUDE.md's record of the old split is corrected in the same commit.

## Comments

`Decided` **is** the shape triage answers — `type` closed to
`models.DECISIONS`, validated in this process. `friday/tools/classify.py` is
deleted and the asserted tool list is nine.

**`for_event` has its caller now.** Ticket 04 shaped `FridayState` for it and
declined to build it, because a constructor with no caller is the speculative
generality this board is otherwise removing. Triage is where a message's
journey starts.

**D20 is a flag, not a sentence.** `NeedsHuman.out_of_set` tells an invented
type from an outage, raised by the answer tool's own body rather than by the
run — because a model that answers wrongly twice overruns `max_turns` and the
run returns `None` having seen no reply at all, so the only thing that knows
the answer was *refused* rather than *absent* is the tool that refused it.
Arguments that are not a JSON object deliberately do **not** raise it: that is
the model saying nothing, not naming something.

**A turn-budget bug this change exposed.** A run whose answer fits ends on its
*first* turn — the terminator finishes it the moment the tool returns an
instance — so the call and its result are not two turns. Triage and the
extractor each passed `extra_turns=1` on top of the one `run_structured` adds,
buying a second correction nobody decided on, on the highest-volume path in
the system. Both pass nothing now.

**`stop_when` is deleted.** Triage was its only caller and no longer needs a
predicate. That is the shape ticket 01 deleted `ask_clarification` for, and the
rule cuts the same way when the code is this board's own.

**A guard that could not fire, replaced by one that can.** `_means` refuses a
task type that never wrote down what it means — and the check is *not* "is the
docstring empty", because a `@dataclass` always has one: absent its own, Python
synthesises the constructor signature. Found by writing the empty-case guard
first and watching its test fail to fail.

Two mutations survived the first pass, both guards written deliberately and
never tested; both have tests that bite now.

**The eval run is owed and is ticket 03's.** CLAUDE.md wants accuracy, a
confusion matrix and the threshold table reported alongside a change to
triage's prompt. Running it now would produce an "after" with no "before",
against a set too thin to catch a regression — the measurement D16 calls
worthless. The runner is ready and reports the new out-of-set number.

## Review

`/code-review` against `bee022f`, Standards and Spec as parallel subagents.
**Both axes independently found the same defect, and it was the one this board
was opened to kill, reintroduced in triage.** `Decided.type` defaulted to
`skip`, `fits` drops unknown keys by design, and every remaining field had a
default — so an empty answer, or one using the deleted tool's own former field
name `task_type`, validated cleanly into a silent discard. That is CLAUDE.md's
"Never drop a mention". Neither field has a default now.

`out_of_set` was also counting failures it then described wrongly: it fired on
any validation failure, so a malformed `confidence` was reported under "the
model named a type that does not exist". `fits` returns which fields failed now
(`structured.Unfit`), rather than the caller matching a substring against a
sentence written for a model.

Four smaller findings, all verified before acting: a stale Layout row in
CLAUDE.md, a flag cleared in the wrong place so a plain `run()` could read it
stale, a comment that contradicted its own test, and two tests reaching through
the harness into the SDK. Two judgement calls declined with reasons.

See the commit `Ticket 07 review: a silent discard I introduced, and five
smaller findings`. Every fix carries a test, deleted once and watched go red.
