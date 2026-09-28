---
labels: wayfinder:map
---

# Domains plug in without touching the core

## Destination

Lock the design of Friday's **agent-building framework** for the Python backend
(`friday/` + `plugins/`): **one durable spine** that every task runs on, where a
plugin is a **domain**, each task type is an **action** (an intent plus a
contract), and a **Planner** writes each run's main-flow `Plan`, handing its
phases to plugin-registered agents. Five threads:

1. **Taxonomy** — domain = plugin; action = intent + contract.
2. **Dynamic registration** — adding a domain or action touches (almost) nothing
   in the core: triage, registry, prompt, config and board all read from the plugin.
3. **The spine** — Intake → Plan → GatePlan → Run → Draft, durable end to end,
   with bounded, re-gated replans (`docs/research/durable-spine-dynamic-plans.md`).
4. **Tools & agents** — core ones in one place; domain ones inside their
   plugin, registered through the plugin API.
5. **Code standard** — class vs function, file size, one type per concept.

Done when every thread is decided and a build board exists. Decided, not built.

## Notes

- **Domain**: Friday. Read `docs/research/durable-spine-dynamic-plans.md` (the
  spine this board now adopts), `docs/DESIGN.md` § What exists, `CONTEXT.md` §
  Vocabulary, `friday/sdk/plugin.py` (the plugin contract), and the boards this
  sits beside: `build-the-loop` (graph → loop rework, done — its graph becomes
  the first plan) and `the-task-contract` (the per-type contract; "the contract
  IS the Plan" — this board is where that Plan gets built).
- **Skills every session consults**: `grilling` + `domain-modeling`.
- **Standing preferences**: design questions in **Vietnamese**, one at a time,
  simple; code and docs in **English**.
- **Plan, don't do.**
- New terms (*domain*, *action*, *toolset*, *step type*, *model tier*,
  *enricher*, *spine*) go to `CONTEXT.md` § Vocabulary at build time.

### Reference: one API-error message, end to end (target shape)

`✓` exists today · `◆NN` new, decided on this board (ticket number).

```
 Reporter on Discord: "@Friday POST /v1/onboarding/completed returns 400" + curl
        │
        ▼
 1. INBOX ✓            record + dedup; the pasted curl stored as an artifact
        ▼
 2. TRIAGE (1 model call)                                                 ◆02
        prompt = core reasoning + every registered action's recognition
                 reasoning + operator-confirmed DB examples
        → backend.trace_problem @ 0.9   (nothing clear → low → operator)
        ▼
 3. TASK + POOL ✓      task of type backend.trace_problem; pool claims it
        → starts the durable spine workflow task-<id> (DBOS)             ◆14
        ▼
 ╔═ DURABLE SPINE — every step durable, resumes after a crash ═════════════╗
 ║ 4. INTAKE (no model)                                               ◆04 ║
 ║      core:   request_text, reported_at, correlationId, curl artifact,  ║
 ║              matching memory + skills                                   ║
 ║      domain: backend enricher → env, service, cluster/namespace/app,    ║
 ║              repo, stack                                                ║
 ║      → placement_identity (env, service, clone, repo)                   ║
 ║ 5. ACKNOWLEDGE ✓ (spine, the action's hook, once per task)        ◆15 ║
 ║      → outbox, no approval: "looking at the logs of <service>…"         ║
 ║ 6. PLANNER (model, inside the action's contract)               ◆12 ◆01 ║
 ║      in:  intake context + trace_problem's contract (allowed step      ║
 ║           types, toolsets backend.logs + backend.code, budget, tier)   ║
 ║      out: Plan v1 (data)                                           ◆10 ║
 ║           phase 1 agent backend.diagnose (backend.logs + backend.code)  ║
 ║           phase 2 draft report                                          ║
 ║ 7. GATEPLAN                                                        ◆11 ║
 ║      schema · within contract · no escalation · budget → freeze + hash  ║
 ║      (fails → re-plan, over N → HandOver)                               ║
 ║ 8. RUN (WorkflowRunner, each step memoized by DBOS)                ◆13 ║
 ║      phase 1 diagnose agent loop ✓: read_log → L1..Ln, read_code,       ║
 ║         what_code_means → ends with one of:                             ║
 ║           Diagnosis (cause, refs [L12,L13], conclusive, alternatives)  ║
 ║           Ask (ask_reporter) ─────────────────────┐                    ║
 ║           HandOver (hand_over) ────────────┐      │                    ║
 ║      after each step: transient → retry · tail wrong → patch + re-gate ║
 ║      · goal gone → abort · over max_replans → HandOver                 ║
 ║ 9. CHECK THE RESULT (the-task-contract)                            ✓/◆ ║
 ║      code: every ref resolves in what was read; judge runs shadow      ║
 ║ 10. DRAFT ✓  phase 2 → operator brief + the reporter's reply           ║
 ╚═══════════════════════════╪═══════════════════╪══════╪══════════════════╝
                             ▼                   │      │
 11. APPROVAL ✓   the Reply waits for the operator on the board
                             ▼                   ▼      │
 12. OUTBOX ✓ (DBOS, never sends twice) → Discord; HandOver → operator only
                                                        │
 ASK BRANCH ────────────────────────────────────────────┘               ◆14
   run pauses; the question goes to the reporter (no approval)
   reporter replies → INTAKE re-runs over the whole conversation
     ├ placement_identity unchanged → continue, the reply added to context
     └ changed (another env/service) → the Planner re-plans
```

Four model calls on the happy path: triage, the Planner, the diagnose
agent, the responder writing the draft (plus the judge once calibrated). Intake, the gate, the runner and the
outbox are plain code. Only the acknowledgement and an `Ask` reach the reporter
without approval; an answer that states a cause always waits for the operator.

## Decisions so far

<!-- Settled in the charting conversation, 2026-09-27. -->

- **One domain = one plugin.** A plugin covers one system area and owns every
  action in it, plus its step types, toolsets, sources, memory kinds and config.
- **The initial catalog**: `backend.trace_problem` (the "trace a failure" half of
  `devops.api_issue` — it *replaces* api_issue, not beside it),
  `backend.answer_question` (the "how does this rule work" half of api_issue
  **plus all of `docs.doc_question`** — the docs plugin goes),
  `ops.request_permission` (was core `access_request`; `ops` holds other
  operational actions later).
- **Naming**: `<domain>.<verb_object>` in snake_case — one convention for
  actions, memory kinds and agents; the action folder name equals the action name.
- **The spine, not per-action graphs** (Q17): every task runs on one durable
  spine — Intake → Plan → GatePlan → Run → Draft → Approval → Outbox. A plugin no
  longer contributes a graph; it contributes **agents and toolsets**; a
  **Planner** writes the run's main-flow `Plan` (data), `GatePlan` validates and
  freezes it, a `WorkflowRunner` runs its phases. Customising *how the work
  flows* is prompt/declaration; a *new capability* is still a coded agent or
  toolset — Friday never runs model-written code.
- **An action is an intent + a contract** (Q18): triage picks an action; the
  action carries no graph but a contract (detail: the action-contract ticket).
  The Planner plans **inside** that contract. (Joins `the-task-contract`: the contract is the Plan's
  frame.)
- **Triage is assembled, not written**: the core keeps only the domain-agnostic
  reasoning; **each action declares its own recognition reasoning and a few
  examples**; triage loads all of them. `config.yaml` `triage_examples` is
  deleted; operator-confirmed DB classifications still feed in as examples.
- **No order and no catch-all label** in triage: nothing fits clearly → low
  confidence → the operator decides (existing `needs_human` path).
- **The extractor is deleted outright** — with the `Params` classes, the
  validation DSL and `ask_for_details`. Understanding the request is the
  Planner's and the agents' job. A reporter's reply re-runs Intake over the whole
  conversation (enriching context); `placement_identity` unchanged → the run
  continues with the reply added; changed → re-plan. Hard to reverse → an ADR.
- **Intake is core + a domain enricher**: the core provides the common context
  (request text, reported-at, uuid/artifact hints, memory/skill retrieval); a
  domain registers an enricher (backend: `Placement`) and **defines its own
  `placement_identity`**.
- **Tools are toolset factories**: `api.toolset(name, factory)` builds tools per
  run (they carry the run's placement and `Evidence`); the MCP tools a plugin's
  sources need are declared through the same API. **One `tool` decorator**; core
  tools become core toolsets (`core.skills`, `core.memory`).
- **The plugin owns tools; the action is granted them** (Q19): toolsets are
  split **by data source** — e.g. `backend.logs` (read_log), `backend.code`
  (read_code, what_code_means), `backend.docs` (read_docs), `backend.db`
  (query_db) — matching `sources/` and the sensitivity GatePlan checks. An
  action's contract names the toolsets it may use (`trace_problem`: logs + code;
  `answer_question`: code + docs), so a tool is written once and shared.
- **Plugins are discovered, in one place**: every package under `plugins/` with
  a `PLUGIN` loads; `config.yaml` only switches one off.
- **`config.yaml` holds only provider keys and named model tiers** (+ install facts, see ticket 07) (named freely,
  e.g. `sonnet-fast`, `super-strong`). Code picks a tier by name; an undeclared
  tier refuses the boot.
- **Class vs function**: a class when it holds a long-lived resource or state; a
  function otherwise — a step-type implementation is a plain function, no factory
  returning a closure.
- **Files**: Python modules snake_case, long descriptive names; **200 lines is a
  soft target** — split by responsibility. `development-rules.md` corrected at
  build time.

- [The action contract under the spine](issues/01-is-the-action-spec-the-task-contract.md):
  an `Action` = name + recognition + an `ActionContract` (allowed step types /
  agents / toolsets, constraints, approval policy, acceptance template, total
  time + max replans); only the contract travels with the frozen `Plan`. The Plan
  is a **main-flow plan** (goal, hypotheses, what to check first, done-criteria,
  which agent owns each phase) — every data access happens inside a named,
  plugin-registered **agent** that drives itself; steps shrink to `agent / ask /
  hand_over / draft`. Budget lives on the agent; the enricher on the domain.
  Names: `Action`, `ActionContract`, `Task`, `Plan`, `Outcome`.
- [The recognition reasoning and the assembled triage prompt](issues/02-the-recognition-reasoning-and-the-assembled-triage-prompt.md):
  `Recognition` = `means` + `pick_when` + `not_when` (signal → other action,
  one-sided — replaces the ordered ladder) + `examples`. Label meaning lives in
  the prompt only; the schema keeps a closed `Literal`. Examples add up
  (plugin → core `skip` → DB-confirmed); labels render sorted by name, `skip`
  last; boot refuses dangling `not_when`, shared or missing examples.
- [Core intake and the domain enricher](issues/04-core-intake-and-the-domain-enricher.md):
  core `intake()` = seed (text, time, raw hints) → the domain's one enricher
  (one type, backend `Placement` incl. its hints; identity a declared field
  subset) → core retrieval keyed by the domain's `retrieval_keys()`. DB-only,
  no network; `release_tag` leaves `Placement` — diagnose reads the running
  version itself. `ops`: no enricher, identity `()`.
- [What replaces `params` for the responder, the board and the pool](issues/05-what-replaces-params.md):
  responder reads the intake context (+ `Outcome` when present), same
  no-invention rule; nothing persisted — `tasks.params` dropped via Alembic
  with the rename migration; board card = opening message text + triage
  `reason` from its `decision_params`; pool line = first line of the opening
  message; no per-action schema.
- [The plan schema and the step vocabulary](issues/10-the-plan-schema-and-step-vocabulary.md):
  `Plan` = task, action, version, `replaces`, contract, goal, a **straight
  list** of steps `agent / ask / hand_over / draft` (`reads` = earlier steps);
  `draft` = core responder model, fixed prompt. **Amends 01**: no hypotheses,
  no per-case done-criteria. Hash = whole plan; results keyed by content
  `step_key`, so a replan reuses identical steps.
- [GatePlan — what a plan must pass before it runs](issues/11-gateplan.md):
  plain code, per version: schema+shape (stop) → contract (refuse, never clip)
  → limits (`max_steps`, new in `contract.limits` — **amends 01**; unfinished
  steps' time ≤ time left) → freeze+hash. Sensitivity/egress deferred to its
  trigger, lands in the gate. Refusal → all errors to the Planner, 2 rewrites
  (core constant, not `max_replans`) → `HandOver`. Operator sees plans on the
  board, never approves them; one plan line on the approval card.
- [The Planner](issues/12-the-planner.md): one core agent, always runs;
  sees intake context (incl. memory/skills), the contract, a new short
  `description` per agent/toolset, optional `Action.planning` (outside the
  contract). Tools `core.memory`/`core.skills` only; strong tier and budget
  as core constants, time counts toward `total_time`. Gate refusal → same
  conversation; failure → `HandOver` `planner_failed`; replan → fresh
  conversation. Code-graded plan-shape eval on synthetic fixtures.

- [The WorkflowRunner and adaptive replan](issues/13-the-runner-and-adaptive-replan.md):
  runner walks steps, skipping any with a stored result at `(task_id, step_key)`;
  agents get a core terminal tool `replan(reason, found)`; the Planner runs only
  on a signal; step failure → 2 retries (core constant) → `HandOver`
  `step_failed`; crash resume per step; a `Replan` is stored and reused like any
  result, so abort is just a plan ending in `draft`; `max_replans` counts every
  replan (reply-driven too) → `replans_exhausted`; time check before each step →
  `out_of_time`.

- [`trace_problem`'s graph becomes the first plan](issues/15-trace-problem-becomes-the-first-plan.md):
  plan v1 = `p1 agent backend.diagnose [logs, code]` → `p2 draft`; the four
  step types hold. Acknowledge is a **spine** step (after Intake, before the
  Planner, once per task, no approval) driven by an optional
  `Action.acknowledge(IntakeContext)` hook. The report file and
  `reports_dir` go; board + draft brief replace them. No fallback, no exemplar.

- [Where the core's behaviour knobs live](issues/07-where-core-behaviour-knobs-live.md):
  `config.yaml` = provider keys + tiers + **install facts** (incl.
  `mention_types`, `concurrency`) — **amends** the config decision above. No
  board settings. Every other knob is a named constant beside its user (agent
  budget/temperature on the agent declaration; `confidence_threshold` changes
  only with a triage-eval table); `daily_token_budget`, `devops.timeout_seconds`,
  `max_asks`, `auto_ask_for_details`, `use_responder`,
  `extraction_budget_tokens` deleted.

- [Renaming the actions and relabelling history](issues/08-renaming-and-relabelling-history.md):
  no data migration — back up, wipe `friday.db`, `upgrade head` on empty (never
  ran for real); chain kept + one schema migration dropping `tasks.params`.
  All names at once, no legacy alias (`backend.trace_problem`,
  `backend.answer_question`, `ops.request_permission`, `backend.<kind>`,
  `backend.diagnose`); operator memory re-imported. Eval relabelled 1:1 by
  hand (boundary cases → 06). Web reads `[{name, domain}]`, colour per domain.

- [The plugin API surface](issues/03-the-plugin-api-surface.md):
  `Plugin(id, register, enricher, config)`; `register` runs once, declares
  only: `api.action`, `api.agent(AgentSpec)` (a declaration the core runs via
  the Harness; core adds `ask_reporter`/`hand_over`/`replan`),
  `api.toolset(ToolsetSpec(factory, mcp={server: TOOLS}))`, `memory_kind`,
  `reader` unchanged. **`deps` and `caps` deleted** — a factory builds tools
  per run from a core `RunContext` (domain, evidence, narrowed `Reads`,
  config). Seven offline boot refusals.

## Not yet specified

- **Getting smarter** — plan exemplars (stored, retrieved, scored by outcome),
  a reflection step; durable-spine §8. Needs real runs first.
- **Invalidation sophistication** — guard steps the Planner inserts vs a world
  cursor the runner checks; how much is enough before data exists.
- **The `ops` domain beyond `request_permission`.**
- **Hand-off between actions** — a question that turns out to be a failure, or
  the reverse: re-plan inside the run, re-triage, or hand over?
- **Core agents' tiers** — triage, responder, room summary (the Planner's is decided: strong).
- **Guards that keep it true** — structural tests once the API shape lands.
- **What happens to `build-the-loop` ticket 4** (resume) — likely subsumed by
  the spine's pause/resume ticket.

## Out of scope

- The `web/` board redesign — beyond reading the action list from the API and
  rendering a frozen plan.
- Friday writing code (a separate charter).
- Plugins living outside the repo, packaging, entry-point discovery.
