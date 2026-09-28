Type: grilling
Status: resolved
Blocked by: 10

# The Planner

## Question

Decide the Planner, which writes the main-flow plan (goal, hypotheses, what to
check first, done-criteria, phases → agents): what it is given (intake context,
the action's contract and its allowed agents' descriptions; exemplars later),
its model tier and budget, how the operator customises it by prompt/declaration
(per action? per domain?), and its failure mode — no valid plan after N tries →
`HandOver`, with what shown. One Planner for every action, or may an action ship
planner guidance? And for a one-phase case, does the Planner still run?

> Note from "The plan schema and the step vocabulary" (10): the Plan has no
> hypotheses and no per-case done-criteria; a step result is keyed by
> `(task_id, step_key)` (content hash), not `(plan_version, phase_id)`.

> Note from "GatePlan" (11): a refused plan comes back with every gate error
> as text; the Planner gets 2 rewrites (core constant, not `max_replans`),
> then `HandOver` with the last plan + errors. How it uses the errors is this
> ticket's call. A replan's time is checked against the time left.

## Answer

Decided 2026-09-28 (grilling).

```
Planner            one core agent, the same for every action, always runs
├── in             IntakeContext (incl. retrieved memory + skills)
│                  · the action's ActionContract
│                  · description of each allowed agent and toolset
│                  · Action.planning (optional)
│                  · on replan: current plan + stored step results + reason
├── toolsets       core.memory · core.skills   (later core.plans) — core-fixed
├── tier           a strong tier, named by a core constant
├── budget         core constants (max_turns, time); counts toward total_time
└── out            Plan (ticket 10) → GatePlan (ticket 11)
```

1. **Always runs, for every action**, one-step cases included. No fixed plan
   per action (that is Q17's per-action graph back) and no fallback plan (it
   would hide a Planner fault the operator should see).
2. **Agents and toolsets gain a short `description`** (1–3 sentences), written
   for the Planner, like a tool docstring — separate from the agent's own
   `instructions`. The Planner sees each allowed agent's name, description,
   result shape name, terminal tools and budget. Missing description → boot
   refuses.
3. **`Action.planning`**: optional prose the plugin author writes, beside
   `recognition` — domain knowledge for planning ("correlationId given → have
   diagnose find it first; env unknown → `ask`"). Outside the contract, not in
   the frozen plan (same reason ticket 01 kept `recognition` out). Without it
   the Planner works from the contract and the descriptions alone; domain
   knowledge never goes into the core prompt.
4. **The operator steers the Planner only through admin memory and skills**,
   already retrieved per case by Intake (ticket 04). No board field, no
   `config.yaml` section.
5. **The Planner is an agent loop with light read tools** — `core.memory`,
   `core.skills`, later `core.plans` (exemplars, under "Getting smarter").
   The list is fixed by core, outside the contract; no plugin toolset ever
   reaches it. The Planner reads *what Friday knows*; only agents read *the
   reporter's system*. GatePlan checks nothing about it (internal, read-only).
6. **Tier: strong**, named by a core constant. A bad plan (wrong brief, a
   needless `ask`) steers the whole run and the gate checks rules, not
   quality.
7. **Budget: core constants** (`max_turns`, `time`), same for every action.
   Planner time counts toward the task's elapsed time, so GatePlan's
   `total_time − elapsed` includes it. Out of budget with no plan = one gate
   refusal (spends one of the 2 rewrites).
8. **A gate refusal continues the same Planner conversation**: the refused
   plan and its errors are appended to its `message_history` as a new
   message; what it already read is kept. GatePlan stays its own spine step;
   each refused version is stored. Distinct from Pydantic AI's own
   schema-retry inside one Planner call — the two do not add up.
9. **Failure** (2 rewrites spent) → `HandOver` with reason `planner_failed`,
   carrying the last plan, every version's gate errors and what the Planner
   read — shown on the task card, told apart from a `hand_over` step the
   Planner chose. Operator only; the reporter gets nothing beyond the
   acknowledgement. The reason is countable: frequent `planner_failed` means
   fix the prompt, descriptions or tier.
10. **Replan: the same Planner and prompt, a fresh conversation.** Input adds
    the current plan, the stored results of finished steps (e.g. `Diagnosis`,
    or the agent's wrong-direction reason) and the replan reason. Output: plan
    v(n+1), `replaces` = previous hash; identical steps reuse results by
    `step_key`. Each replan gets its own 2 rewrites. Unlike 8, the world moved
    since the last plan; the stored results summarise it.
11. **Eval: code-graded, synthetic fixtures** — intake context → expected plan
    *shape* (terminal step `draft | ask | hand_over`, agent, toolsets); `brief`
    is not graded. The Planner replays by cassette with fixed memory/skills.
    Scoring real quality waits for real cases (as `build-the-loop` did). A
    change to the Planner's prompt runs this eval, like triage's. Building it
    is a build-board ticket.

Carried: to **13** — how the runner hands the Planner a replan (input as in
10). To **14** — the Planner's `message_history` must persist between the
Planner and GatePlan steps for a refusal rewrite to resume. Map fog "Core
agents' tiers" — the Planner's part is decided (strong).

## Amended 2026-09-28 by ticket 17

The Planner's budget is core constants `(max_turns, tokens)`; no time, nothing counts toward a `total_time`. See [The budget in three groups](17-the-budget-in-three-groups.md).
