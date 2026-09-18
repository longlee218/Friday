# 14: A diagnosis is scored against cases the operator labelled

**What to build:** `evals/api_issue.jsonl` (frozen `Evidence` + the
operator's cause, refs and `conclusive`), a builder from marked tasks, a
runner printing cause accuracy, `conclusive` agreement and groundedness.

**Blocked by:** 05, 06. **Decisions:** finding L; research 01.
**Status:** ready-for-agent — the labels are `ready-for-human`

## Notes
Evidence is frozen so a run calls the model only. The mark that labels a row
is the mark that verifies that task's `finding` (finding D). Baseline first
(D16 on `nothing-runs-unmeasured`), then tools-on vs tools-off (finding K).
