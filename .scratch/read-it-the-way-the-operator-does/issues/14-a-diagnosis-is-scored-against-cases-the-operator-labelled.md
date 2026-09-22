# 14: A diagnosis is scored against cases the operator labelled

**What to build:** `evals/api_issue.jsonl` (frozen `Evidence` + the
operator's cause, refs and `conclusive`), a builder from marked tasks, a
runner printing cause accuracy, `conclusive` agreement and groundedness.

**Blocked by:** cases the operator has labelled (2026-09-22) — no longer
05 and 06. **Decisions:** finding L; research 01.
**Status:** not started, but **no longer blocked by code** (2026-09-22).
The mechanism it needs now exists: a *captured case* under `data/cases/`
holds the reporter's parameters, the raw answers the log back end gave, and
the operator's own `decisive` line and `cause`. It replays offline through
the real graph with one command, so a frozen row calling the model only —
which is what this ticket asks for — is a format change away.

What it is blocked by is **the set**. There is one case. Three runs of it
agree, which says the answer is stable and says nothing about accuracy.
Cases 1–5 cannot supply the rest: they are dev cases already past their
pod's retention. The set grows one captured production case at a time, and
labelling each is the operator's.

## Notes
Evidence is frozen so a run calls the model only. The mark that labels a row
is the mark that verifies that task's `finding` (finding D). Baseline first
(D16 on `nothing-runs-unmeasured`), then tools-on vs tools-off (finding K).
