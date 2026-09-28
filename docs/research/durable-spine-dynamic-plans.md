# After v2 — a durable spine and dynamic plans

**Status: research / direction, 2026-09-23. Not built, not an accepted
target.** This is the step that follows the DESIGN-v2 migration (§15),
not a replacement for it — it builds on the state v2 leaves behind
(Pydantic AI harness, DBOS-backed workflows, the plugin registry, the
`sdk` / `kernel` / `plugins` split). It is written here in `research/`,
like `pydantic-ai-migration.md`, because it needs the same treatment v2
got before it becomes a target: a review pass and an operator decision.
Where this document and DESIGN-v2 disagree on the *near* term, v2 wins —
this is about the term after it.

The one-line version: **v2 gives every task type its own graph; the step
after v2 gives every task *one* durable spine and moves the per-task
variation into a `Plan` — data a `Planner` agent produces and a
`WorkflowRunner` executes.** Orchestration becomes model-*proposed*
(a plan, validated and frozen), never model-*driven* (control flow
decided live). This refines, rather than reverses, the §1 non-goal
"workflows stay deterministic Python".

---

## 1. Where this sits relative to v2

v2's task types register a `graph: Callable[[Deps], DAG] | None` (§6.1);
today only `api_issue` has a multi-node graph and every other type takes
the one-node simple graph. The v2 migration draws the seams — the plugin
API, the DBOS workflow port, the memory-kind registry — but keeps the
*shape* of a run per task type.

This direction keeps all of v2's seams and changes what fills them:

- The router no longer maps a task type to a graph. There is one graph —
  the spine — and it is the same for every task.
- What differs between an `api_issue` run and a `doc_question` run is the
  **plan** the spine's `Planner` node produces, not the graph.
- Plugins stop contributing graphs and start contributing **step types**
  (the vocabulary a plan is written in) plus the toolsets, sources,
  sub-agents and memory kinds those steps use.

Nothing below is buildable before v2's steps 2–3 (Pydantic AI, then DBOS)
land: the `WorkflowRunner` and the plan vocabulary have to be drawn on
the real workflow port, and the `Planner` is a Pydantic AI agent whose
*output* is a plan.

## 2. Prior art (verified 2026-09-23, primary sources)

Every serious system converges on **a fixed outer path with a dynamic
inner agent**; none lets the model drive the whole control flow in a
system that also wants durability and safety.

| System | Outer | Inner | Note |
| --- | --- | --- | --- |
| **BabyAGI** | fixed 4-step loop (pull → execute → embed → create+prioritize) | LLM at every node | mutable task queue re-derived each iteration; **no stopping/budget bound** in the original |
| **AutoGPT classic** | none — autonomous self-prompting loop | LLM decides each step | the pure (b) form |
| **AutoGPT platform (current)** | **block-based visual workflow graph**; NL → agent graph | model at specific blocks | the project *evolved away from* the open loop toward a fixed graph |
| **Hermes** (Nous) | fixed **Gateway** (adapter → authorize → session key) | one dynamic `run_conversation` loop | no per-type routing; less structured inside than Friday |
| **OpenClaw** | fixed **Gateway** router (sessions, routing, dispatch) | dynamic inner agent | bounded pre-reply memory retrieval (Active Memory) before the reply |

Two readings that shape this design:

- **AutoGPT's own trajectory is the strongest signal**: the pure
  model-driven loop did not survive contact with production; the project
  now ships a fixed graph with model calls at nodes. Friday should not
  re-derive that lesson.
- **BabyAGI's missing bound** (an infinite loop with no convergence
  condition) is the failure this design must foreclose: every plan is
  bounded and every replan is counted.

Sources: `github.com/yoheinakajima/babyagi_archive`,
`yoheinakajima.com/birth-of-babyagi`,
`github.com/Significant-Gravitas/AutoGPT`,
`github.com/NousResearch/hermes-agent` (`website/docs/developer-guide/architecture.md`),
`docs.openclaw.ai` (`plugins/architecture`, `plugins/reference/active-memory`).
Unverified from primary sources: AutoGPT-classic loop guards (README
only); OpenClaw Active-Memory "blocking sub-agent" semantics and its
per-tool approval model.

## 3. The core shift

| v2 | after v2 |
| --- | --- |
| task type → its own `graph` | one spine for every task |
| triage routes to a type's DAG | triage admits + gives a coarse intent; **no route-to-graph** |
| `extraction` (node 0) extracts params per type's schema | `Intake` enriches context generically; **`Plan` subsumes param extraction** |
| a plugin contributes a graph | a plugin contributes **step types** + capabilities |
| the run's shape is fixed at boot | the run's shape is a **plan**, produced per run, validated, frozen |

The reason `extraction` "no longer fits" (the observation that started
this): per-type extraction presupposes the type and its param schema are
already known. Once the spine is universal and a `Planner` decides how to
approach the case, extracting a fixed param set per type is redundant —
understanding the request is the `Planner`'s job, and what it needs to
read is expressed in the plan.

## 4. The durable spine

```
        KERNEL CHAIN around every node (§8): budget · recording · redaction · needs/side_effect
        DBOS: every node is a durable step · resume from the last incomplete step
──────────────────────────────────────────────────────────────────────────────────────
Inbound ─► Triage ─► [Pool] ─► Intake ─► Plan ─► GatePlan ─► Run ──► Draft ─► [Approval] ─► Outbox
 dedup    admit +    claim     enrich   Planner  validate   Workflow  outbox   operator     send
 cursor   coarse     concur    context  → Plan   schema·    Runner    row      decides      kernel
          intent               (memory  (JSON)   needs·     runs the           §3.3/§3.4    only
                               summary           sensitiv·  frozen
                               skills)           freeze     plan          │
                                        ▲                      │ after each step: check
                                        │                 INVALIDATED? (assumption · staleness · cursor)
                                        │                      ├ transient   → retry step (bounded)
                                        └─ replan v(n+1) ◄─────┤ local       → PATCH the tail, keep the head
                                           (RE-GATE, bounded)  ├ fundamental → ABORT → draft "situation changed"
                                                               └ over max    → HANDOVER
──────────────────────────────────────────────────────────────────────────────────────
        HITL: Ask / HandOver — pause and resume through DBOS, callable from any node
```

| Node | Role | vs v2 |
| --- | --- | --- |
| **Inbound** | stream, dedup on `(channel, message_id)`, cursors, never drop an addressed event | unchanged |
| **Triage** | *lighter:* is this addressed / in scope / a coarse intent | **drops route-to-DAG**; §10's `core:intake` fallback still applies |
| **Pool** | concurrency, claim, help-wanted asks, one-agent-per-db (§12.1) | unchanged; orthogonal to shape |
| **Intake** | generic context enrichment: memory (`fact`/`constraint`/`decision`/`finding`), room summary, `when:`-matched skills, related tasks; bounded by a token budget | **replaces `extraction`**; no per-type param extraction |
| **Plan** | the `Planner` agent produces a `Plan` (JSON). It runs the Pydantic AI loop, but its *output is a plan, not actions*. Memoized by DBOS; bounded | **new** — the dynamic brain |
| **GatePlan** | validate the plan as data before executing | **new** — safety-critical |
| **Run** | the `WorkflowRunner` interprets the frozen plan, deterministically | **new** — replaces the per-type DAG |
| **Draft** | result → outbox row; voice by identity | unchanged |
| **Approval / Outbox** | §3.3 / §3.4 state machine, frozen+hashed payload, send is the outbox loop | unchanged |

## 5. Plan as data

A `Plan` is a small DSL over a **closed vocabulary of step types
registered by plugins**:

```json
{
  "goal": "diagnose 500 on /checkout",
  "budget": { "max_steps": 8, "max_tokens": 40000 },
  "on_obstacle": "replan",
  "steps": [
    {"id":"s1","type":"read_source","source":"devops:loki","args":{...},"needs":["source:loki"]},
    {"id":"s2","type":"assert","check":"service == s1.service"},
    {"id":"s3","type":"sub_agent","agent":"diagnose","toolsets":["devops:logs","devops:code"],"input_from":["s1"]},
    {"id":"s4","type":"draft","audience":"reporter","input_from":["s3"]}
  ]
}
```

- A starting vocabulary (~5–7): `read_source`, `call_tool`, `sub_agent`,
  `gather` (parallel), `decide` / `branch`, `assert` / `guard`,
  `write_memory` (→ candidate), `draft`.
- **Each step type is code the kernel or a plugin implements.** The
  runner interprets a plan over this closed set; it never executes
  arbitrary model-generated code. This is what keeps the run bounded and
  the §5.3 side-effect classes enforceable.
- The step's `needs` and the step type's declared side-effect are what
  `GatePlan` and the kernel chain check — the same machinery v2 already
  applies to tools (§6.2).

Why data and not code: a plan that is data can be validated, frozen,
hashed, shown on the board, replayed and diffed. A plan that is generated
code cannot. This is the outbox pattern (§3.4 — freeze the payload, hash
it, act on the stored bytes) applied to execution.

## 6. GatePlan

Because the plan is data, it is inspectable before it runs. The gate:

1. **Schema-valid** against the registered step vocabulary.
2. **Capability check** — every step references only a capability the
   run's scope and `needs` grant; no privilege escalation.
3. **Sensitivity / egress (§9.2, §5.3)** — the run's reachable surfaces
   are computed *from the plan*, the provider is chosen, and the
   lethal-trifecta opt-in is enforced. v2 decides sensitivity "once, at
   run start" from the task type's `needs`; here it is decided once, at
   the gate, from the plan — a small shift that keeps the invariant while
   the shape is now dynamic.
4. **Bound** — step count and estimated budget within limits.
5. **Freeze + hash** the plan.
6. On failure: re-plan (bounded), then `HandOver`.

The plan can be rendered on the board and on the approval card — the
operator sees what the machine intends to do before it does it.

## 7. Adaptive execution

"Frozen" means **immutable within one execution attempt**, not forever. A
plan is *versioned*; replanning mints a **new version** through
`GatePlan`, rather than mutating a running plan in place. DBOS memoizes a
completed step by `(plan_version, step_id)`, so an attempt resumes from
its last incomplete step and a replan reuses — never re-runs — the work
already done.

After each step the runner runs `check(assumptions · staleness · world
cursor)`:

| Detected | Action |
| --- | --- |
| transient (a source errors / returns empty) | retry the step (bounded) |
| local (only the tail is wrong) | **PATCH**: replan the tail, keep the head |
| fundamental (the goal itself is no longer valid) | **ABORT gracefully** → draft "situation changed, here is what I found, no action" / close |
| ambiguous, or over `max_replans` | **HANDOVER** to the operator |

The two cases that motivated this:

- **"No longer adequate"** (a step's result breaks a plan assumption —
  the error is in service Y, not X) → PATCH the tail from the new facts.
- **"Outdated"** (the world moved under the run — a follow-up arrived, the
  incident self-resolved, a deploy landed) → the staleness / `_overtaken`
  check (already in v1's outbox, §3.4) fires; replan against the new
  state, or ABORT if the goal is gone.

Two invariants that cannot bend:

1. **A replan re-passes `GatePlan`.** The new plan may touch new surfaces,
   so sensitivity / egress / `needs` are re-checked. A mid-run replan must
   never smuggle a step past the gate.
2. **Replans are bounded** (`max_replans`, e.g. 2–3). Past the bound →
   `HandOver`. This is the guard against replan-thrash — the bound
   BabyAGI lacked.

This is the plan-and-execute "replan node" pattern (LangGraph
plan-and-execute, Plan-and-Solve). What Friday adds over them: each
version is durable (DBOS resume) and each replan is re-gated (safety).

## 8. Getting smarter

Friday runs on API models, one operator, one machine — there is no
fine-tuning. "Smarter" means **improving what enters the next run's
context**: memory, examples, skills, and a new **plan-exemplar library**.
Every durable thing learned passes a **candidate gate** (anti-poisoning)
and is measured by an **eval** (does it actually help?). These two are
what separate this from an autonomous loop that drifts.

**Operator-taught (high signal, near-free):**

- **Editing a draft before approving** — the diff between drafted and
  approved is a label: tone → `voice`, content → `finding` / `decision`.
  The operator already reviews every draft; most systems discard this
  signal.
- **Re-typing a task** → a triage `example` for the scope (§10 — active
  at once as an explicit human mark, with provenance, retirement on
  type-version change, and a per-type cap).
- **Rejecting a plan at the gate** with a reason → a negative exemplar +
  a `decision`.
- **Writing a skill** (`when:` frontmatter) and **admin memory** through
  the board → direct teaching.

**Self-taught (automatic, bounded, gated):**

- A **post-run reflection node** critiques the plan and its execution →
  a `finding` *candidate*.
- A **plan-exemplar library** — approved plans are stored (keyed by
  problem shape) and retrieved by the `Planner` as few-shot; failed plans
  are stored as anti-examples.
- **Implicit outcome signals** — approved-without-edit (+), rejected /
  redone (−) — rank exemplars automatically (they are operator actions,
  hence trusted).
- **Source / tool reliability** — a step that repeatedly errors → a
  `finding` the `Planner` reads.

**The gate, by provenance:**

- *Ranking / weights* (which exemplar to prefer) update automatically —
  the signal is an operator action.
- *New durable content* (a `finding`, a `fact`) goes through the
  candidate stage (`propose_memory` → `resolve_candidates_for_message`,
  already in `store/db.py`) and is resolved by the operator, or
  auto-promoted only when the provenance is trusted and the risk low.
- **An untrusted-content run always writes candidates, never auto**
  (§9.4) — one injected log line must not become a lasting `finding`.

**Measurement and forgetting:**

- A change to behaviour (a new example, skill, or exemplar) re-runs the
  triage eval (`evals/run_triage_eval.py`) plus a regression net built on
  `replay_case.py` — the line between self-improving and self-poisoning
  (CLAUDE.md rule 4).
- Examples retire on type-version change; caps per type/scope; `finding`
  rows expire (`valid_to`, §9.4 on trigger); the board can disable an
  example or exemplar. Without this, memory becomes noise — BabyAGI's
  other failure.

Three nested loops: **fast** (per task — draft-edit, re-type) →
**medium** (across tasks — reflection, exemplars) → **slow** (periodic —
eval, retire drift).

## 9. Plugins in this world

A plugin no longer contributes a graph. It contributes:

- **step types** — the vocabulary the `Planner` may compose;
- **toolsets, sources, sub-agent types, memory kinds, skills** — what
  those steps use.

The spine is universal, so the kernel names no task type and no graph —
G1 in a stronger form than v2 (§6.1 still let a plugin ship a graph). A
plugin extends the *vocabulary a plan can be written in*, and the
`Planner` composes across every enabled plugin's step types.

## 10. What each piece builds on / changes

| Piece | Builds on (v2) | Changes |
| --- | --- | --- |
| Spine as one graph | the DBOS workflow port (§7) | the router maps nothing to a graph |
| `Plan` / `WorkflowRunner` | `sdk/workflow.py` node/step/edge types | a plan is data; the runner interprets it |
| `Planner` | the Pydantic AI harness (§6.7) | an agent whose output is a plan |
| `GatePlan` | run-start sensitivity (§9.2), egress (§5.3) | decided from the plan, not from `needs` |
| `Intake` | the extraction runner (§6.1 node 0) | generic enrichment, not per-type params |
| Step types | toolsets + `side_effect` / `needs` (§6.2) | the unit a plugin contributes |
| Plan exemplars | memory kinds + candidates (§9.2, §9.4) | a new kind, gated like the rest |
| Adaptive replan | DBOS resume (§7), `_overtaken` (§3.4) | versioned plans, re-gated |

## 11. Invariants preserved

1. A plan is frozen within an attempt → DBOS resume is deterministic.
2. The step vocabulary is closed and maps to registered capabilities →
   no arbitrary code runs.
3. `GatePlan` validates before `Run`; **a replan re-passes the gate**.
4. Every plan is bounded (steps, budget); every run bounds its replans →
   no infinite loop.
5. Durable learning goes through the candidate gate; untrusted-content
   runs always write candidates (§9.4).
6. A behaviour change is measured by an eval before it counts.
7. No plugin has a send verb; send is the outbox loop, kernel-only
   (§5.3).
8. Every model and tool call is wrapped by the kernel chain (§8) — the
   kernel does not delegate its §3.3 invariants to DBOS or to a plugin.

## 12. Migration position

After DESIGN-v2 §15 completes (through step 10). Incremental, each step
leaving the suite green:

1. Define the `Plan` schema and the closed step-type vocabulary (~5–7
   types), in `sdk`.
2. Build the `WorkflowRunner` as a new shape alongside the existing DAGs.
3. Port `api_issue`'s hand-written graph to a `Plan`.
4. Add the `Planner` and `GatePlan`; replace `extraction` with `Intake`.
5. Add adaptive replan, the reflection node, and the exemplar library.
6. Retire per-type DAGs; lighten triage.
7. An ADR recording the refinement of the §1 non-goal ("model-proposed,
   validated, frozen; not model-driven live").

## 13. Open questions to design first

- **The plan schema and step-type vocabulary in detail** (each type's
  input / output / `needs` / side-effect). This decides both the power
  and the safety of the whole system — it is the first thing to nail.
- **Plan exemplars**: how they are stored and retrieved (keyword first;
  embeddings only when substring search is measured to miss, per §16),
  and how the outcome signal scores them.
- **Invalidation detection**: guard steps the `Planner` inserts vs a
  world-cursor the runner checks — how much sophistication is enough.
- **The `Planner`'s own budget and failure mode**: a `Planner` that
  cannot produce a valid plan after N tries hands over; where N sits and
  what the operator sees.
