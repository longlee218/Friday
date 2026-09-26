---
labels: wayfinder:map
---

# The task contract

## Destination

Lock the design of a **single per-task contract that is also the run's Plan**
(one artifact, not two) — the one place that gathers what today is scattered
(`MAX_READS`, a tool's `needs`, outbox approval, the grounding gate, the token
budget) plus a new, **verified** `acceptanceCriteria`. Decided, not built. Done
when the shape, who authors each part, how it is verified, and how it lands
incrementally are all settled.

Scope: the contract/Plan design and its verification. **Not** Friday writing
code (a future charter change — see Out of scope), and **not** the read-only
graph rework (that is `build-the-loop`, which runs in parallel).

## Notes

- **Domain**: Friday. Read `docs/research/durable-spine-dynamic-plans.md` (the
  `Plan` / `GatePlan` this contract merges with), `docs/DESIGN.md` § What
  exists, and `evals/api_issue.py` (the "an LLM critic is an eval variant until
  it agrees with the operator" rule — load-bearing for the verifier).
- **Skills every session consults**: `grilling` + `domain-modeling`.
- **Standing preferences**: design in **Vietnamese**, code/docs in **English**.
- **Sits beside** `durable-spine`, **above** `build-the-loop`. The contract IS
  the durable-spine `Plan`, extended with the contract fields — not a second
  layer (charting decision Q3).
- **Motivating problem**: without verified `acceptanceCriteria` an agent
  "self-feels done" — exactly the ticket-2 bug (tests green, feature inert),
  caught only by an independent reviewer.
- **Plan, don't do.**

## Decisions so far

<!-- Settled in the charting conversation, 2026-09-26. -->

- **The contract IS the Plan** — one per-run artifact, not a frame + a separate
  plan. GatePlan validates the Plan against its own contract fields. (Q3)
- **Hybrid, two-part** (Q1=c): **type-level constants** (`constraints`,
  `allowedActions`, `approvalPolicy`, `budget`) authored once per task type by
  the operator; **instance-level** (`objective`, `acceptanceCriteria`) per case.
  Constants are inherited, not re-authored per task.
- **`acceptanceCriteria` is model-proposed, operator-confirmed** per instance;
  the operator writes the per-type template. (Q2)
- **Verified by a separate agent** (generator/evaluator split), never by the
  doer — the antidote to "self-feels done". The verifier is **not trusted until
  calibrated against the operator's marks** (the repo's LLM-critic rule); code
  checks where cheap (refs resolve, conclusive⇒alternative — already the
  grounding gate), model-judge only where needed.
- **Friday stays read-only now.** The contract is shaped so a future
  code-writing charter is a *widening* of `allowedActions` / `acceptanceCriteria`
  (patch / test-pass / no-regression), not a rewrite — gated by an ADR and a
  safety redesign when it comes.
- **Consolidation is the point** (Q3): the contract replaces the scattered
  config as the single source of truth.
- [Can the contract land before durable-spine?](issues/01-can-the-contract-land-before-durable-spine.md):
  **(a) incremental, staged by dependency** — not coupled to full durable-spine.
  The **type-level** constants + a **static per-type `acceptanceCriteria`
  template** + the **renamed deterministic grounding gate** land early (once
  `build-the-loop` settles the shape, ~after ticket 6); the **per-instance
  model-proposed** criteria, the full dynamic Plan, and the verifier **agent**
  (needs calibration via the cassette-eval) wait for durable-spine's `Planner`.
- [The merged Contract/Plan schema](issues/02-the-merged-contract-plan-schema.md):
  two names, one runtime artifact — `TaskContract` (per-type template: the
  consolidated `constraints`/`allowed_actions`/`approval_policy`/`budget` +
  `acceptance_template`) and `Plan` (per-run: carries its contract + instance
  `objective`/`acceptance`/`steps`). `Acceptance = {name, check: code|agent,
  description}`. Only the `TaskContract` half + naming the deterministic gate
  lands now; the `Plan`'s instance fields wait for the `Planner`. Stub:
  `contract_plan_STUB.py`.
- [How the verifier agent is trusted](issues/03-how-the-verifier-agent-is-trusted.md):
  two tiers, not a code-vs-agent choice. **Grounding (`check="code"`)** is the
  floor (every claim resolves to a cited `Lnn`; also blocks a citation-stuffed
  Judge); the **correctness Judge (`check="agent"`)** is load-bearing because the
  read-only diagnosis has no executable ground truth. The Judge scores a
  **rubric** (G-Eval style), runs **shadow-only** against operator marks on the
  cassette cases, and **gates only after ≥10 labeled cases agree** — erring
  strict (a false-"pass" is worse than a false-"fail"). A FAIL → **`HandOver`**
  naming the failed criteria + reasoning (never silent "done"); bounded replan
  waits for durable-spine. The Judge is **independent**: fresh context, artifact
  + evidence only, never the doer's chain-of-thought.

## Not yet specified

- **The code-writing widening** — what `allowedActions` / `approvalPolicy` /
  `acceptanceCriteria` become when patch / test / regression enter, and the
  safety redesign (Friday modifying the repo, running tests = execute; lethal
  trifecta when it both reads untrusted logs and writes code). Cannot be
  ticketed until the charter actually flips; sketched here as the signpost.
- How the contract interacts with `build-the-loop`'s `Intake` / grounding gate
  once both exist (the diagnose task's acceptanceCriteria vs its grounding gate).

## Out of scope

- **Friday writing code / patches** in *this* effort — a separate future
  charter (reverses `friday-never-writes-code`), designed only far enough here
  that the contract can grow into it.
- The read-only graph rework — `build-the-loop`.
- The durable-spine execution engine itself (`WorkflowRunner`, replan) — this
  effort designs the contract the Plan carries, not the runner.
