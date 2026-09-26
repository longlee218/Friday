Type: grilling
Status: resolved
Blocked by:

# Can the contract land before durable-spine?

## Question

The contract **is** the Plan (map decision), and the Plan is durable-spine's
construct — which is a *later* effort. But the motivation ("consolidate the
scattered config, stop the agent self-feeling-done") is wanted **now**, on the
read-only diagnose loop `build-the-loop` is building.

Decide the sequencing:

- **(a) Incremental**: a **static per-type contract** lands now — a degenerate
  Plan with no `Planner`, its `steps` fixed, read by the current graph. It
  consolidates `MAX_READS` / `needs` / approval / grounding into one per-type
  object, adds an operator-authored `acceptanceCriteria` template, and a
  verifier agent. When durable-spine's `Planner` arrives, it fills the
  instance-level parts and the same object becomes the dynamic Plan.
- **(b) Coupled**: the contract only exists once durable-spine's `Plan` /
  `Planner` / `GatePlan` land; until then the config stays where it is.

Turns on: is a contract without a `Planner` still a contract, or is
per-instance `acceptanceCriteria` (model-proposed) the whole point — which needs
a model pass the current graph doesn't have?

If (a): what is the smallest slice — does it touch `build-the-loop`, or ride
after it? If (b): what unblocks it, and does anything land sooner?

This decision gates the schema ticket and everything downstream.

## Answer

Decided 2026-09-26. **(a) incremental, staged by dependency** — not a binary
"now vs wait for durable-spine". The contract's parts have different
dependencies:

1. **Type-level object lands early** (Q1): consolidate `constraints` (the
   grounding gate), `allowedActions` (tool `needs`), `approvalPolicy` (outbox),
   `budget` (`MAX_READS`/token/timeout) into one per-type `ApiIssueContract`. A
   pure refactor with **no `Planner`/durable-spine dependency** — this is the
   "one place" the effort is for.
2. **`acceptanceCriteria`: static per-type template now** (Q2), operator-authored
   (api_issue e.g. "cause cites ≥1 `Lnn` + ≥1 alternative considered +
   not_checked stated"). Per-instance **model-proposed** criteria wait for
   durable-spine's `Planner`.
3. **Verifier split by readiness** (Q3): the deterministic acceptance checks
   already exist as the grounding gate `_judged` (refs resolve,
   `conclusive ⇒ alternative`) — name them "acceptance checks" and land now. The
   verifier **agent** (model-judge criteria) waits for calibration against
   operator marks via the `build-the-loop` cassette-eval (the LLM-critic rule).
4. **Sequenced after `build-the-loop`** (Q4): consolidate the *new* shape, not
   the config of nodes about to be deleted (`FindRequestLog`/`ReadFailingCode`,
   ticket 5). Ride after ~ticket 6 (rewire).

Net: the contract is **not coupled to full durable-spine**. Its type-level half
+ the acceptance template + the renamed deterministic gate land incrementally
once the graph settles; the dynamic half (per-instance proposal, full Plan,
verifier-agent) waits for the `Planner` / the eval. Unblocks ticket 02: fields
split into "inherited now" vs "generated later".

