# 14: A diagnosis is scored against cases the operator labelled

**What to build:** `evals/api_issue.jsonl` (frozen `Evidence` + the
operator's cause, refs and `conclusive`), a builder from marked tasks, a
runner printing cause accuracy, `conclusive` agreement and groundedness.

**Blocked by:** cases the operator has labelled (2026-09-22) — no longer
05 and 06. **Decisions:** finding L; research 01.
**Status:** **a precondition now** (2026-09-22), not the nicety this board
has been treating it as.

Architecture v3.3 hands the investigation to a model with tools. That makes
it non-deterministic: the same case may be investigated two ways, and "the
suite is green" says nothing about whether the cause it reaches is better or
worse than the fixed pipeline's. Without a set of labelled cases there is no
way to answer that at all — so this stops being something to get round to and
becomes the thing v3.3 waits on.

The mechanism exists: a captured case under `data/cases/` holds the
parameters, the raw answers the back end gave, and the operator's own
`decisive` line and `cause`. It replays offline with one command.

**What the operator actually does, which is less than this ticket implied:**
Friday captures the case and *proposes* both labels; they confirm or correct.
The same shape as `friday/memory/verdicts.py`, where the operator marks a
classification right. What cannot be delegated is the judgement — the true
cause of a production incident is a fact about their system.

**It accumulates rather than being sat down to.** One case exists. Ten to
twenty make a score worth reading, and they arrive as incidents do.
## Notes
Evidence is frozen so a run calls the model only. The mark that labels a row
is the mark that verifies that task's `finding` (finding D). Baseline first
(D16 on `nothing-runs-unmeasured`), then tools-on vs tools-off (finding K).
