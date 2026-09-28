Type: grilling
Status: resolved
Blocked by:

# The action contract under the spine

## Question

An action is now an **intent + a contract** (no graph). Decide the contract's
exact fields and how it relates to `the-task-contract`'s type-level
`TaskContract` (constraints, allowed_actions, approval_policy, budget,
acceptance_template): one object or two, and which name wins.

Candidate fields: name; recognition reasoning + examples (triage); allowed step
types; allowed toolsets; model tier(s); budget (steps / tokens / time / max
replans); acceptance criteria; approval policy; intake enricher and
`placement_identity`. Which overlap (toolsets ≈ allowed_actions), what replaces
`TaskTypeSpec.graph`, and where its dead fields (`extractor`, `needs`) go.

## Answer

Decided 2026-09-27 (grilling).

**Two objects, not one.** An `Action` carries a contract; only the contract
travels with the `Plan` (frozen and hashed with it). Triage text inside every
frozen plan would be noise, and rewording it would change every plan's hash.

```
Action                      (a plugin registers it; replaces TaskTypeSpec)
├── name                    e.g. backend.trace_problem
├── recognition             recognition reasoning + a few examples   → read by triage
└── contract: ActionContract (the-task-contract's TaskContract, renamed)
    ├── allowed_step_types    agent · ask · hand_over · draft
    ├── allowed_agents        e.g. backend.diagnose
    ├── allowed_toolsets      e.g. backend.logs · backend.code
    ├── constraints           e.g. "every ref points at a line that was read"
    ├── approval_policy       e.g. Reply waits for approval
    ├── acceptance_template   this kind of work's definition of done
    └── limits                total time · max replans

Agent                       (a plugin registers it, named, shared across actions)
└── instructions · result shape · terminal tools · model tier ·
    maximum toolsets · per-run budget (max_turns, tokens, time)

Domain (the plugin)         intake enricher + placement_identity
Task                        one concrete piece of work (a DB row)
Plan                        one per task: main flow, hypotheses, what to check first,
                            done-criteria, which agent owns which phase; carries the
                            contract; frozen + hashed
Outcome                     Ask | Reply | HandOver   (renamed from `Action`)
```

Sub-decisions:

- **`allowed_actions` splits** into `allowed_step_types` and `allowed_toolsets`
  (checked differently: plan shape vs data touched), which also frees the word
  "action". The **plugin author** writes both lists in code, at registration —
  the ceiling; the Planner picks a subset per run and never adds; GatePlan
  refuses anything outside. Neither `config.yaml`, the board nor a model can
  widen them.
- **A Plan is a main-flow plan, not a program.** It states the goal, the opening
  hypotheses, what to check first, the done-criteria and which agent owns each
  phase. **Every data access happens inside an agent**, which drives itself (as
  the built `diagnose` loop does). The step vocabulary shrinks to `agent`, `ask`,
  `hand_over`, `draft` — no `read_source` / `call_tool` steps. Safety stays in
  the contract: an agent is only ever built with toolsets the contract allows.
  Within a phase the agent adapts; the Planner revisits only between phases or
  when an agent reports its hypothesis was wrong. A separate Planner (not the
  agent planning in its first turn) keeps the plan written in a clean context,
  visible on the board before anything runs, and able to propose per-case
  acceptance criteria.
- **Agents are named and plugin-registered** (`api.agent(...)`); the contract
  lists `allowed_agents`. At run time an agent gets the intersection of the
  contract's toolsets and its own maximum. The model tier lives on the agent, so
  the contract has no model field.
- **Budget lives on the agent only** (per-run turns / tokens / time); the
  contract keeps total time and max replans. Consequence: a case's total cost is
  the sum over its phases, bounded only indirectly by total time — whether to cap
  the number of phases is left to the GatePlan ticket.
- **The enricher and `placement_identity` belong to the domain**, not the action
  (both backend actions share one).
- **Names in code**: `Action`, `ActionContract`, `Task`, `Plan`; the old
  `Ask | Reply | HandOver` union becomes `Outcome`.
- **Deleted from `TaskTypeSpec`**: `params`, `extractor`, `graph`, `needs`. Where
  the per-run `deps` factory lives is left to the plugin-API ticket.

## Amended 2026-09-28 by ticket 17

The agent's per-run budget is `(max_turns, tokens)` — no time; the contract's limits drop total time and keep max replans (and `max_steps`, ticket 11). See [The budget in three groups](17-the-budget-in-three-groups.md).
