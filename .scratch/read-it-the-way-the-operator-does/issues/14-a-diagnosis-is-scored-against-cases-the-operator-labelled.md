# 14: A diagnosis is scored against cases the operator labelled

**What to build:** `evals/api_issue.jsonl` (frozen `Evidence` + the
operator's cause, refs and `conclusive`), a builder from marked tasks, a
runner printing cause accuracy, `conclusive` agreement and groundedness.

**Blocked by:** cases the operator has labelled (2026-09-22) — no longer
05 and 06. **Decisions:** finding L; research 01.
**Status:** built (2026-09-22), and it has one case in it.

`evals/api_issue.py` scores a run against what the operator said was true;
`uv run python -m evals.run_api_issue_eval` replays every captured case with
the model on. Cause accuracy is substring matching against `cause_mentions`,
not a judge model — the spec's own rule, and its weakness is written in the
docstring rather than hidden.

Three things it refuses to flatter: an **unlabelled** case scores zero rather
than full marks, a **voided** answer is a third outcome and not
`conclusive: false`, and under ten cases it prints that it is a regression
check and **not a score**.

**What is left is the set, and it accumulates.** One case: cause 1/1,
conclusive 1/1, refs 2. Friday proposes both labels when it captures a case
and the operator confirms or corrects — the `verdicts.py` shape. Ten to
twenty make a number worth comparing, and they arrive as incidents do.
## Notes
Evidence is frozen so a run calls the model only. The mark that labels a row
is the mark that verifies that task's `finding` (finding D). Baseline first
(D16 on `nothing-runs-unmeasured`), then tools-on vs tools-off (finding K).
