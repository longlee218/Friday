Status: ready-for-human
Blocked by: 14

# The Planner hands over goals, not methods

Amends: [The plan schema and the step vocabulary](../../domains-plug-in/issues/10-the-plan-schema-and-step-vocabulary.md) §5,
[GatePlan](../../domains-plug-in/issues/11-gateplan.md) §2 (toolsets),
[The runner and adaptive replan](../../domains-plug-in/issues/13-the-runner-and-adaptive-replan.md) §1 (`replan`).
Source: operator design review, 2026-09-30, against the ticket-14 working tree.

## The problem

The spine splits one task into two thinkers: the **Planner** decides the
workflow (which steps, which agent, what the task must establish) and the
**agent** decides how to investigate inside its step. The boundary is:

```
Planner   "Find why POST /v1/orders returns 500; use logs + code."
Diagnose  "How exactly do I find it?"  — its own execution plan, internal
```

Three places in the code let the Planner cross that line, and all three are
cheap to fix. Two of them bite harder than they look because the Planner never
sees the reporter's system, only what Intake and memory say about it, while
the agent reads it.

### 1. `brief` asks for the method

`planner_prompt.py` INSTRUCTIONS and `AgentStep`'s docstring say a brief
"says what to establish **and where to look first**". Decision 10 §5 moved
hypotheses out of the plan for exactly this reason (the Planner guesses before
reading anything, and a guess anchors the agent) but kept "what to look at
first" in the brief, and its worked plan is procedural: "find the 400 for the
correlationId; read the validator at the running tag; say which field…".

Meanwhile `backend.diagnose` already carries its own execution plan
(`diagnose_prompt.py` `THINKING` + `READS`: read the error line first, widen
the window, `read_code` on a stack frame). An agent given two "how"s picks one
or blends them, and the one written by the thinker who read nothing is the
worse one. This also blocks the operator's wish to run the Planner on a
cheaper tier than the agent: a plan that carries the method needs the
stronger model; a plan that carries the goal does not.

### 2. `toolsets` is a hard ceiling the Planner sets blind

Each `agent` step names the toolsets it grants; GatePlan refuses anything
outside `contract.allowed_toolsets ∩ spec.toolsets` and the runner builds
tools from `step.toolsets` only (`workflow.py` `_agent`). A brief the agent can
ignore; a missing toolset it cannot. Its one way out is `replan`, which
spends one of `max_replans` (2 on `trace_problem`) and a whole Planner call.

Nothing today values the narrowing: sensitivity/egress, the reason a plan
carries toolsets, is deferred (decision 11 §1: no toolset is egress); the
`core.planner` eval grades toolsets "at least those expected — more is not
wrong". So the design already says a full grant is right, and the Planner is
being asked to guess a subset anyway.

### 3. `replan` does not say when

Decision 13 §7 leaves "local adjustment vs wrong direction" to the agent (the
runner cannot tell them apart). The only guidance the agent gets is the
tool's docstring: "Your brief points the wrong way for this task". That does
not separate the two cases the operator review drew:

```
need more information within the toolsets you have   → keep reading
need a toolset or agent you were not granted,
or the brief's premise is wrong                      → replan
```

Without it a model either replans over every dead end (agent → Planner →
agent for nothing) or never replans and hands over.

## What is decided

- **A brief is a goal and its constraints, never a method.** The Planner
  says what the step must establish and what the reporter gave (ids,
  endpoints, times); the agent's own instructions say how. Amends decision
  10 §5: "what to look at first" leaves the brief.
- **An empty `toolsets` means the full grant, `contract ∩ ceiling`.** Filled
  in by code before the plan is hashed, so `step_key` and the `plans` table
  see the real grant. The Planner lists toolsets only when the action's
  `planning` or a constraint gives it a reason to narrow; GatePlan's refusal
  rule for a grant *outside* the ceiling stays. Amends decision 11 §2.
- **The `replan` tool carries the rule** above in its docstring, and the
  `retriage` tool is untouched. No new types: `Replan(reason, found)` stays a
  string with line ids; a structured `ReplanRequest` (requested
  capabilities, typed evidence) waits until a second agent or a second
  toolset grant exists to ask for. Amends decision 13 §1 only in wording.
- **The output contract is not a new field.** The review proposed
  `expected_output` on the step; `AgentSpec.result` (`Diagnosis`) and
  `contract.acceptance_template` already are it.

## Goal

- `friday/kernel/spine/planner_prompt.py`: INSTRUCTIONS say a brief is what
  to establish plus what the reporter gave, and that `toolsets` may be left
  empty for the full grant; `PlannedStep.brief` / `toolsets` docstrings match.
- `friday/kernel/spine/plan.py`: `AgentStep` docstring; nothing else (the
  fill happens before a `Plan` is built).
- `to_steps` (or `planner.py` `_write`, whichever sees the contract and the
  agent spec first): an empty `toolsets` on an `agent` step becomes
  `sorted(contract.allowed_toolsets & set(spec.toolsets))`; an unregistered
  agent stays empty for GatePlan to refuse as it does now.
- `friday/kernel/harness/run_agent.py`: `replan`'s docstring states the rule.
- `evals/datasets/planner/`: expectations unchanged (a full grant satisfies
  "at least these"); add one case whose action `planning` asks to narrow, so
  the Planner's remaining reason to list toolsets is exercised.
- Decisions 10, 11, 13 gain an `## Amended <date> by build-the-spine ticket
  20` section; the worked plan in 10 is rewritten as a goal.
- `docs/DESIGN.md` § What exists (the Planner paragraph, `run_agent`):
  brief = goal, empty toolsets = full grant. `CONTEXT.md` § Vocabulary:
  *brief*.

## Not in this ticket — with the trigger that opens it

Recorded so the review is not lost; none has a case to build against today.

- **Shared `Evidence` across steps.** Each agent run gets a fresh
  `Evidence()` (`workflow.py` `_agent`), so step B cannot cite step A's
  `L12`; steps talk through `reads` (typed results) only. Trigger: the first
  plan with two `agent` steps where the second must point at the first's
  lines.
- **A plan-level acceptance gate.** Today the only mechanical acceptance is
  `AgentSpec.check` (`diagnose.check`: refs resolve, conclusive ⇒ a rival);
  `contract.acceptance_template` is a sentence the Planner reads and no code
  checks; per-case `done_criteria` was dropped in decision 10 §6 pending a
  calibrated judge. Trigger: an action whose `acceptance_template` is not
  already carried by its one agent's `check`, or a calibrated judge
  (`build-the-loop`, after real runs).
- **`PlannerFailed` as its own type**, not a `HandOver` subclass. The
  metric the review wants (`planner_failure_rate` apart from
  `handover_rate`) is already countable by the `planner_failed:` prefix
  (decision 12 §9). Trigger: the board or an eval needing to tell them apart
  by type rather than by reason text.
- **Context projection per step (`context_reads`), a Context Planner, a
  Context Resolver, a context budget, a shared blackboard.** Intake already
  is the context pack for one domain (seed → enricher → memory/skills, rebuilt
  every pass, so fresh by construction); every step gets all of it, which is
  fine at ≤ 4 steps and one domain. A mutable blackboard would break
  `step_key` memoisation (results are keyed by step content). Trigger: a
  second domain, or a plan whose steps need different halves of the context.
- **Workflow templates beyond the contract.** `ActionContract` (allowed
  agents / toolsets / step types / limits) + `Action.planning` already is the
  grammar the Planner instantiates. The review's fix-bug / feature / benchmark
  workflows produce code and patches, which Friday never writes (DESIGN D6);
  they do not apply.

## Acceptance

- [x] A plan with an empty `toolsets` on an `agent` step freezes with the
      full `contract ∩ ceiling` grant, and its `step_key` equals the same
      plan written with the grant spelled out (test).
- [x] A grant outside the ceiling is still refused, never clipped (existing
      test stays green).
- [ ] `core.planner` run and reported: no case worse than the 6/8 baseline;
      the narrowing case passes.
- [x] `replan` docstring states the rule; the suite's tool-description guard
      (if any) updated.
- [x] Decisions 10, 11, 13 amended; `docs/DESIGN.md`, `CONTEXT.md` corrected.
- [x] Whole suite green; `code-review` done.

## Left for the operator (2026-09-30)

- **`core.planner` run**: done after 21 — 6/8, but not "no case worse":
  `question-about-the-docs` passed at baseline and now hit `planner_failed`
  (request limit), while `permission-for-what` moved the other way. See
  `evals/README.md`. Run it again before ticking the box.
- **The narrowing case** in `evals/datasets/planner/` was not added: the
  grader only checks "at least these toolsets", and no registered action
  gives a reason to narrow. It needs an `excludes` check and a synthetic
  action; open it when a real narrowing reason exists.
- The suite and both reviews are done; the last box waits on the eval run.

## Amended 2026-09-30 by ticket 22

"The Planner lists toolsets only when … a reason to narrow" is withdrawn:
`PlannedStep` has no `toolsets`, and every agent step gets the full grant.
The narrowing case above is therefore not needed. See
[ticket 22](22-the-grant-is-code-not-the-planner-and-memory-writes-apart.md).
