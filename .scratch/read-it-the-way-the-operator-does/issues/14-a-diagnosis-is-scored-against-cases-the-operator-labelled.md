# 14: A diagnosis is scored against cases the operator labelled

**What to build:** `evals/api_issue.jsonl` (frozen `Evidence` + the
operator's cause, refs and `conclusive`), a builder from marked tasks, a
runner printing cause accuracy, `conclusive` agreement and groundedness.

**Blocked by:** cases the operator has labelled (2026-09-22) — no longer
05 and 06. **Decisions:** finding L; research 01.
**Status:** built (2026-09-22), with one case in it, and **its consumer is
paused**.

`evals/api_issue.py` scores a run against what the operator said was true;
`uv run python -m evals.run_api_issue_eval` replays every captured case with
the model on. It still earns its place with the fixed pipeline: it is the
only thing that notices a change to the distillation making causes worse,
and three such changes landed on 2026-09-21 with nobody able to tell.

**The set accumulates as incidents do.** Friday proposes both labels when it
captures a case; the operator confirms or corrects. One case today: cause
1/1, conclusive 1/1, refs 2. Ten to twenty make a number worth comparing —
and that number is what ticket 15 waits on.
## Notes
Evidence is frozen so a run calls the model only. The mark that labels a row
is the mark that verifies that task's `finding` (finding D). Baseline first
(D16 on `nothing-runs-unmeasured`), then tools-on vs tools-off (finding K).
