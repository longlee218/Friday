Type: prototype
Status: resolved
Blocked by:

# The plan schema and the step vocabulary

## Question

Stub the `Plan` as decided in the action-contract ticket: a **main-flow plan**,
not a program — goal, opening hypotheses, what to check first, done-criteria
(per-case acceptance), and ordered **phases**, each owned by a named agent with
the toolsets it is granted and the phases it reads from. The step vocabulary is
small and core-owned: `agent`, `ask`, `hand_over`, `draft`.

Decide each step type's fields and output, how a phase's result feeds the next,
how the plan carries its `ActionContract`, versioning (`plan_version`) and what
gets frozen + hashed. Show one worked plan for `backend.trace_problem`.

## Answer

Resolved 2026-09-28 (prototype + grilling). Prototype: branch
`prototype/plan-schema-and-step-vocabulary`, file
`.scratch/domains-plug-in/plan_schema_and_step_vocabulary_STUB.py` — run it to
see plan v1, a replan v2 that reuses p1 by key, and what the shape rules refuse.

```
Plan
├── task_id · action · plan_version (1, +1 per replan)
├── replaces        hash of the version it replaced (None for v1)
├── contract        ActionContract, copied in, frozen with the plan
├── goal            this case's goal, one sentence
└── steps           a straight list of:
    agent      id · agent · toolsets · brief · reads
    ask        id · question · reads
    hand_over  id · reason · reads
    draft      id · reads
```

1. **A straight list** — no branch, no parallel. A change of direction is a
   replan: a new version through GatePlan. Shape rules: ids unique; exactly
   the last step is terminal (`draft | ask | hand_over`), none before it;
   `reads` names only earlier steps; a `draft` reads something.
2. **How results flow**: every step gets the `IntakeContext`; `reads` names
   the earlier steps whose stored result the runner hands in. An `agent`
   step outputs its agent's declared result shape (`backend.diagnose` →
   `Diagnosis`), or `Ask` / `HandOver` from its terminal tool, which stops
   the plan there. `ask` / `hand_over` steps are the Planner deciding the
   same thing up front (vague request / out of reach).
3. **`draft` is written by the core responder model** (not rendered by
   code): it reads `reads` + the intake context under one fixed prompt, with
   the no-invention rule. Output: `Reply`, which waits per
   `approval_policy`. This adds a model call to the happy path (triage,
   Planner, diagnose, responder).
4. **No per-plan draft guidance** — the draft step has only `reads`. One core
   prompt vs one per action → ticket 07 (core behaviour knobs).
5. **No `hypotheses`** — *amends ticket 01*, which listed opening hypotheses
   and "what to check first" in the Plan. The Planner guesses before reading
   anything, worse than the agent that reads; a guess can anchor the agent;
   `diagnose` already weighs rivals in `alternatives_rejected`. What to look
   at first goes in the step's `brief`. Revisit under "Getting smarter" once
   real runs exist.
6. **No per-case `done_criteria`** — also amends ticket 01. The check uses
   `contract.acceptance_template` directly; per-case criteria wait until the
   judge is calibrated (the only thing that could check them).
7. **Freeze + hash**: `plan_hash` = sha256 of the canonical JSON of the whole
   plan, contract included. Results are not in the plan.
8. **Memo key = step content, not `(plan_version, step_id)`**: `step_key` =
   hash(the step's fields minus `id`/`reads` + the keys of the steps it
   reads). Results are stored by `(task_id, step_key)`. A replan's identical
   step reuses its result automatically — no `kept` field, and the Planner
   cannot claim a step is done; change a step and it and everything reading
   it re-run.

Worked plan (`backend.trace_problem`, prod-onboarding-400):
`p1 agent backend.diagnose [backend.logs, backend.code] "find the 400 for the
correlationId; read the validator at the running tag; say which field or rule
rejects it"` → `p2 draft reads p1`.

Carried: to **13** — ticket 13's question says `(plan_version, phase_id)`;
this answer replaces that with `(task_id, step_key)`; how an agent signals
"wrong direction" without hypothesis ids (its result carries a reason the
Planner reads). To **14** — `step_key` excludes the intake context on
purpose; whether a reply that leaves `placement_identity` unchanged re-runs
the last agent step (it must, to see the reply) is 14's call. To **11** —
the contract checks (agent / toolsets / step types within the contract) and
a cap on the number of steps. To **15** — acknowledge as a step or a spine
concern is not a step type here, so this leans "spine concern".
