# Evals

The regression net for what the suite cannot judge: whether the model's
answers are right. An eval calls the configured provider, so it costs money
and is read by a person; the suite checks only the wiring and the arithmetic.

This folder holds **data only**, and the data is **not in git**:
`evals/datasets/*` is gitignored, like `data/cases/`, because cases are real
channel traffic — pasted curls carry Bearer tokens, pasted tickets carry
customer names, addresses and payment ids. Committed: this README and
`datasets/planner/`, whose cases are synthetic. The code lives where it runs:

| What | Where |
| --- | --- |
| The declaration a plugin registers (`EvalSpec`, `EvalCase`) | `friday/sdk/eval.py`, `api.eval(...)` |
| Running an eval on Pydantic Evals — the one module importing `pydantic_evals` | `friday/kernel/evals/run.py` |
| The case format below (loader, writer) | `friday/kernel/evals/cases.py` |
| `core.triage` — the classifier | `friday/kernel/evals/triage.py`, `triage_scoring.py`, `triage_set.py` |
| `core.planner` — the plan shape | `friday/kernel/evals/planner.py` |
| `backend.trace_problem` — the diagnosis | `plugins/backend/evals/trace_problem.py` |
| The command, and the task each case runs through | `run_eval.py` |

```bash
uv run run_eval.py                               # list the registered evals
uv run run_eval.py core.triage                   # score triage
uv run run_eval.py core.triage --add-confirmed   # add ✅-marked verdicts as cases
uv run run_eval.py core.planner                  # score the Planner's plan shapes
uv run run_eval.py backend.trace_problem             # replay data/cases/ and score
FRIDAY_DB=/path/to/db uv run run_eval.py core.triage
```

## `core.triage` — did it classify the mention right?

### The set: `datasets/triage/`

One markdown file per case, one folder per label. The folder is the label the
case expects; the frontmatter says it again, and a file whose two disagree is
refused.

```
datasets/triage/
  backend.trace_problem/001-a-check-ho-e-xem-sao.md
  backend.answer_question/…
  ops.request_permission/…
  skip/…
  _messages/be-ticket-pod-submit-failed.md    a long message a case names — not a case
```

A case with one message: the body is the message, pasted as is — a paragraph,
a curl, a log, anything.

```markdown
---
expected_task: backend.trace_problem
---

a check giúp e với, curl này trả 500:

    curl -X POST https://api.example.com/v1/orders -d '{"sku":"A1"}'
```

A case that is a turn — several messages in a row, as the channel shows them.
A long message (a pasted ticket, a curl) lives in `_messages/` and is named by
`file:`; `own: true` marks a message the operator's own account sent.

```markdown
---
expected_task: backend.trace_problem
turn:
  - text: "Cứu @Lee (Long Lê) ơi"
  - file: _messages/be-ticket-pod-submit-failed.md
  - text: "check xem nguyên nhân là gì nhé"
    own: true
---
```

A malformed case — no frontmatter, an empty message, a `file:` that is missing
or empty — is refused with its path, never skipped. **Frozen, not queried**:
a run never reads the database, so a prompt edit and a change in what the
operator has since marked land in two numbers, not one nobody can attribute.

### What the set has to cover

`unfit` (`friday/kernel/evals/triage.py`) is the guard, and
`tests/test_triage_eval.py::test_the_set_this_repo_ships_is_fit_to_score_against`
runs it over the local set (skipped on a machine without it):

- **Every label triage may reach has a case** — every registered action plus
  `skip`. The day an action is added, this says the set has not caught up.
- **At least one case is a turn of more than one message** — what the
  classifier is shown in a real channel.
- **No turn appears twice** — a duplicate doubles its own weight.
- **No example the prompt shows the model is a case**
  (`tests/test_assembled_triage_prompt.py`) — scoring it on a sentence it was
  told the answer to measures nothing.

Three more are the operator's judgement, not code's: cases near the boundary
between two labels, cases a correct classifier calls `skip`, and cases that
read like the channel does — Vietnamese, a pasted stack trace, a curl. **The
cases are the operator's to write** (board `every-answer-has-a-shape`, D18): a
classifier scored against labels a model chose measures nothing.

### Adding what was marked ✅

`--add-confirmed` reads the verdicts the operator marked right and writes a
case for each one not already in the set. **It only adds**: a hand-written
case is never rewritten or deleted, and an example the prompt shows the model
is left out. It warns when the set is left unfit, and still writes.

### What a run prints

Accuracy, the confusion matrix, how many cases each confidence threshold from
0.5 to 0.9 would escalate to a human (what `CONFIDENCE_THRESHOLD` is checked
against), how many answers named a label that does not exist, and **every
wrong case by file**.

### When to run it

Per `CLAUDE.md` § Verifying a change: a change to
`friday/kernel/triage/prompt.py`, or to anything upstream of it, is not done
until this has run and its numbers are reported with the change.

### What a run costs

One call per case, plus its one output correction when the answer's shape is
wrong. Measured once, against the original 16-row set on `MiniMax-M3`: ~15,000
input tokens and ~1,100 output tokens for the whole run. The set has grown
since, and a turn or a pasted ticket is much longer than a one-line message;
the run does not record its own cost, so re-measure before relying on it.

---

## `core.planner` — did the Planner write the right kind of plan?

Code-graded on synthetic cases (board `domains-plug-in` ticket 12 §11): each
case is an intake context and the plan **shape** it should come to. Scoring a
plan's quality — its `brief`, its `goal` — waits for real cases.

### The set: `datasets/planner/` (committed)

One folder per case. `case.md` is the case; `skills/<name>/SKILL.md` are the
skills the Planner may read for it (optional).

```markdown
---
action: backend.trace_problem
memory: ["orders-api production logs are in Loki"]    # optional
expect:
  terminal: draft                     # draft | ask | hand_over
  agents: [backend.diagnose]          # the agent steps, in order
  toolsets:                           # optional: at least these, per agent
    backend.diagnose: [backend.logs, backend.code]
---
the request, as the reporter wrote it
```

**The model is live; what it reads is fixed.** The case's `memory` is both
what Intake retrieved and what `memory_search` finds (seeded into a
throwaway store); its skills folder is the whole skill library. So a changed
number is the prompt's or the model's, never the operator's memory.
`tests/test_planner_eval.py` builds the smallest plan of each expected shape
and requires GatePlan to pass it, and that every action and every terminal step
type has a case. The expectations are a person's call — review them when an
action's contract or agents change.

### What a run prints

How many cases got the whole shape right, the count per check (`terminal`,
`agents`, `toolsets` — granted at least those expected; more is not wrong),
how many ended `planner_failed`, and every wrong case with what it got.

### First numbers (2026-09-29, `strong` = `z-ai/glm-5.3-flash`)

Two runs, 6/8 each (terminal 6/8, agents 6/8, toolsets 7/8):

- `trace-too-vague-to-start` ("a ơi bị lỗi api rồi") — planned `diagnose`
  with every toolset instead of asking. Both runs.
- `permission-for-what` ("a ơi e không vào được") — the second run spent all
  3 tries' 10 turns on tool calls and never answered (`planner_failed`); the
  first answered `hand_over` where the case expects `ask`.

### After tickets 20 and 21 (2026-09-30, same `strong` model)

One run, 6/8 (terminal 6/8, agents 6/8, toolsets 7/8, `planner_failed` 1/8);
the run takes over ten minutes.

- `trace-too-vague-to-start` — drafted `diagnose` instead of asking, as in
  both first runs.
- `question-about-the-docs` — `planner_failed`: the Planner spent its
  request limit (11) on read tools and never answered; it passed before.
  The same failure mode `permission-for-what` had in the first numbers.
- `permission-for-what` passed this time. One run each way, so the count is
  unchanged and the case that fails moves: read it as noise until a second
  run says otherwise.
- With `toolsets` empty the diagnose step is granted all four of its toolsets
  (`backend.logs`, `backend.code`, `core.memory`, `core.skills`).

### When to run it

A change to the Planner's prompt — `friday/kernel/spine/planner_prompt.py`
— or its tier, budget or rewrites (`PLANNER`, `PLAN_REWRITES` in
`planner.py`) is not done until this has run and its numbers are reported
with the change. So is a change to what it is shown: an agent's or
toolset's `description`, an action's `planning`.

---

## `backend.trace_problem` — did the investigation reach the right cause?

The suite checks that code does what it was told to; nothing in it notices
the model's causes getting worse. Under architecture v3.3 the model drives its
own reads, so "the suite is green" says nothing about which way diagnoses
better.

**The cases are not in this repository.** A captured case holds raw log lines
carrying `userId`, `ip` and `deviceId`, so cases live in `data/cases/`
(gitignored), one JSON file each. What is here is how a case is scored.

| Label | |
| --- | --- |
| `decisive` | a substring of the log line they call decisive |
| `cause` | the true cause, in their words |
| `cause_mentions` | the tokens any correct answer must contain |
| `conclusive` | whether the evidence really did settle it |

Friday proposes all four; the operator confirms or corrects — the true cause
of a production incident is a fact about their system.

**Substring matching, not a judge model**: an LLM critic is an eval variant
until its scores agree with the operator's marks. It cannot tell a right
answer phrased unusually from a wrong one; it is deterministic, costs nothing,
and catches the cause drifting off what the evidence was about. An unlabelled
case scores zero, not full marks. Under ten cases the report says it is a
regression check, not a score.

A case is replayed through the DAG (`replay_case.run_captured`), which only
the composition root can reach — so `run_eval.py` builds this eval's task
until build-the-spine ticket 14 lets the core run an action on a case.
