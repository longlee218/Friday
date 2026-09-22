# 18: Eval plumbing on pydantic-evals

**What to build:** The triage and api_issue eval *runners* move onto
`pydantic-evals` — its `Dataset`/`Case`/`Evaluator` own case-running, the
per-case report and (optionally) Logfire; Friday keeps its domain scoring
(`evals/scoring.py`, `evals/api_issue.py`) and its frozen sets unchanged. The
numbers a run reports do not change; the loop underneath does.

**Why now (operator, 2026-09-22):** "reuse what the world built beats building
from zero." `pydantic-evals` is pure tooling with no guarantee to lose — no
injection/boundary conflict like the harness's memory/skills features, so it
clears the reuse bar those did not. (It does pull `pydantic-ai-slim`, but that
pin already exists from ticket 05, so it adds no new surface.) Adopting it gives
both evals one shape, a standard report and a Logfire path, and a common home
for suites still to come.

**Blocked by:** 05 (the harness swap; this scores the same live agents).

**Source:** conversation 2026-09-22; `https://pydantic.dev/docs/ai/evals`;
`evals/README.md` (D7 "frozen, never queried"), `CLAUDE.md` § Verifying rule 4.

**Status:** done

- [x] `pydantic-evals` added as a dependency (standalone; not `pydantic-ai-harness`)
- [x] `run_triage_eval.py` runs the frozen set through a `pydantic-evals` `Dataset` of `Case`s and a task that calls the live `Triage`; the confusion matrix, threshold table and out-of-set number still print, computed by the kept `evals/scoring.py`
- [x] `run_api_issue_eval.py` runs captured cases the same way, keeping `evals/api_issue.py`'s `score`/`report`
- [x] `evals/scoring.py`, `evals/api_issue.py`, `evals/triage.jsonl` and the D7 "frozen, never queried" rule are unchanged
- [x] The eval runs deterministically (sequential where a scripted model replays by call order — the triage harness serialises at `_one_run` anyway, so parallelism buys it nothing)
- [x] Tests rewritten to the new runner surface; `test_eval_scoring.py` and `test_eval_dataset.py` stay green; `uv run pytest -q` passes
- [x] A live triage-eval run reported (accuracy, confusion matrix, threshold table) — closes ticket 05's item 7 on the new runner

## Comments

**2026-09-22 — Implemented; one box left for the operator's live run.**
Both runners are `pydantic-evals` `Dataset`/`Case`/`evaluate` now, sequential
(`max_concurrency=1`), each with a per-case `Evaluator` (`Correct` /
`CauseFound`) for the framework's own accuracy view. `run()` still returns
`list[Prediction]` / `list[Scored]`, so `report()` and every caller/test are
unchanged — `test_eval_runner`, `test_eval_scoring`, `test_eval_dataset`,
`test_api_issue_eval` stayed green with no rewrite (signatures preserved). Full
suite: 1604 passed, 1 skipped; mypy clean on both runners. `scoring.py`,
`api_issue.py`, `triage.jsonl` untouched.

**2026-09-22 — Done.** Live run on the new runner: 35 examples, accuracy
100.0%, clean-diagonal confusion matrix, zero out-of-set, thresholds
0.5→1/0.6→2/0.7→4/0.8→6/0.9→13. No regression vs the pre-swap known-good; also
closes ticket 05's item 7.

## Comments

The shape both migrations share: a `Dataset[Case(inputs=<row>, expected_output=<label>)]`,
a task closing over the built agent that returns the domain outcome, and a
custom `Evaluator` scoring exact-match (triage) / cause-mentions (api_issue) so
the framework prints per-case accuracy. The *aggregate* domain metrics
(confusion matrix, threshold table, out-of-set for triage; the `Scored` report
for api_issue) are not things `pydantic-evals` produces — they are computed from
the run's collected outputs by the kept scoring modules, which is the boundary:
**framework owns the plumbing, Friday owns the domain metrics.**
