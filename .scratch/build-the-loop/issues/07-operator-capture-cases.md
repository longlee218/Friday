Status: ready-for-human
Blocked by: 01

# Operator: capture ≥ 10 cases

Decision: [A deterministic eval for a loop that reads what it likes](../../the-graph-becomes-a-loop/issues/02-a-deterministic-eval-for-a-loop-that-reads-what-it-likes.md)
(Q5 — the real gate for deleting the baseline is case count, not the mechanism).

## PARKED until Friday runs against real traffic (2026-09-26)

Friday has **never been launched**, so there are no real `api_issue` tasks in
the store and no reachable incident logs to capture. This task cannot progress
until Friday runs for real. Because of that the effort chose **(B)**: build the
target shape now (Intake → loop → Report) on **synthetic/canned fixtures**, and
defer capture + scoring + calibration to *after launch* — see the map / STATUS.
The "measure before cutting the baseline" discipline is deferred consciously
(there is no running baseline to protect pre-launch); it comes back **as
calibration** once this task can run. This ticket holds the runbook for that
day.

## Goal (operator-driven — a `task`, not a decision)

Grow `data/cases/` to ≥ ~10 captured cases in the superset format (ticket 01),
each with operator-confirmed labels (`decisive`, `cause`, `cause_mentions`,
`conclusive`). Friday proposes the four; the operator confirms/corrects — the
true cause of a production incident is a fact about their system, not the
model's to assert.

Until this lands, `run_api_issue_eval` prints "regression check, not a score"
and ticket 08 stays blocked.

## Acceptance

- [ ] ≥ ~10 cases in `data/cases/`, superset format, labelled and confirmed.
- [ ] Cases read like real traffic (Vietnamese, pasted stack traces, curls),
      per `evals/README.md`'s coverage notes.
- [ ] `run_api_issue_eval` reports a score (≥ 10 cases), not a bare regression
      check.

---

## Runbook — how to score the Diagnose (when real tasks exist)

The mechanism is already built (`evals/run_api_issue_eval.py`,
`evals/api_issue.py`, `replay_case.py`). What is missing is only the data. Steps:

### 1. Capture a case from a real incident
A real `api_issue` report runs through Friday and becomes a task in the store
(id `N`). **While its logs still exist** (a dev pod's history is short — measured
2026-09-21, two probes an hour apart saw different oldest lines), replay it:

```
uv run replay_case.py N --diagnose      # replays the task, runs the model once
```

This runs the same graph against a throwaway copy and prints the diagnosis. A
captured case is one JSON file under `data/cases/` (gitignored — the log lines
carry `userId`/`ip`/`deviceId`) holding: the report metadata (`summary`,
`environment`, `correlation_id`, `curl`, `reported_at`, `channel_id`), the raw
`reads` the back end returned (superset format: `{"superset": ["<rfc3339> <log
line>", …]}`), and the four labels below.

> Note: a one-command `--capture <path>` that writes this file with Friday's
> proposed labels is not built yet (ticket 01's remaining box). Until it is, the
> case JSON is assembled by hand from the replay output. Building `--capture` is
> the first thing to do the day real tasks exist.

### 2. Confirm the four labels (the operator's judgement — not the model's)
Friday proposes; the operator confirms/corrects. Written into the case JSON:

| label | meaning | example |
| --- | --- | --- |
| `decisive` | a **substring** of the log line the operator calls the decisive evidence | `"categoryId should not be empty, categoryId must be a UUID"` |
| `cause` | the **true cause**, in the operator's own words (read by humans, not scored) | `"empty categoryId; NestJS ValidationPipe rejected it before the handler"` |
| `cause_mentions` | the **tokens any correct answer must contain** — this is what is scored | `["categoryId", "ValidationPipe"]` |
| `conclusive` | did the evidence **really settle it** (true/false) | `true` |

`cause_mentions` should be identifying tokens (a field name, a class, an error
code), not a whole sentence. Same shape as `friday/memory/verdicts.py`.

### 3. Score
```
uv run python -m evals.run_api_issue_eval
```
It reads every `data/cases/*.json`, replays each through the graph **with the
model on** (`replay_case.run_captured`, sequential), and scores each with
`evals/api_issue.score`. It prints three numbers plus the per-case rows:

- **cause** `k/n` — the diagnosis's `cause` contained **all** of `cause_mentions`
  (case-insensitive substring). This is the headline number.
- **conclusive agrees** `k/n` — the model's `conclusive` matched the operator's.
  A voided answer (refs/alternatives gate failed, or none produced) counts as
  neither true nor false, printed `voided`.
- **answered at all** `k/n` — the answer survived the node's grounding gates.

Rules baked in (`evals/api_issue.py`):
- **Substring, not a judge model** — deterministic, cheap; cannot tell a right
  answer phrased oddly from a wrong one, nor "X is empty" from "X is not empty".
  Stated weakness, accepted as the right *first* measure.
- **An unlabelled case scores 0**, not full marks — a growing set cannot get
  quieter as it gets weaker.
- **Under 10 cases it prints "regression check, not a score."** Ten to twenty
  make a number worth comparing. This is exactly the ≥10 gate ticket 8 waits on.

### 4. Calibrate the judge (later, the "improve on the data set" step)
The substring score is the floor. The richer verifier is an **LLM judge**, and
the repo rule is load-bearing: *an LLM critic is an eval variant until its scores
agree with the operator's marks* (`evals/api_issue.py`, and the
[the-task-contract verifier decision](../../the-task-contract/issues/03-how-the-verifier-agent-is-trusted.md)).
So when a judge is added: run it **shadow / advisory only** against these
captured cases, compare its verdicts to the operator's labels, and let it gate
**only after ≥10 labelled cases agree**, erring strict (a false "pass" is worse
than a false "fail"). This captured set is the calibration set for that.
