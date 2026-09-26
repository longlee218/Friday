Type: prototype
Status: resolved
Blocked by: 01

# The merged Contract/Plan schema

## Question

Specify, as a stub to react to, the **one** artifact that is both the contract
and the Plan — the hybrid two-part shape:

- **type-level** (operator-authored once per task type): `constraints`,
  `allowedActions`, `approvalPolicy`, `budget { max_steps, max_tokens,
  max_time }`. These map to today's grounding gate, tool `needs`, outbox
  approval, and `MAX_READS`/token/timeout — the ticket names each source it
  replaces.
- **instance-level** (per case): `objective`, `acceptanceCriteria`
  (model-proposed → operator-confirmed), and the `steps` (fixed today; a
  `Planner`'s output under durable-spine).

Show how it maps onto the user's `TaskContract` sketch (objective / constraints
/ acceptanceCriteria / allowedActions / approvalPolicy / budget) **plus** the
durable-spine `Plan` (goal / steps / on_obstacle) — since they are one artifact.
Decide field names against `CONTEXT.md` vocabulary; add the new term(s) there.

Blocked by ticket 01: whether it is a static per-type object now or the full
dynamic Plan decides which fields are inherited vs generated.

## Answer

Decided 2026-09-26 (prototype). Stub to react to:
[`contract_plan_STUB.py`](../contract_plan_STUB.py) (throwaway; fold into real
code at build, then delete).

**Two names, one runtime artifact (A):**
- `TaskContract` — the per-**type** template, operator-authored once: the
  consolidation. Fields map to today's scattered config:
  - `constraints` ← the grounding gate, stated as an invariant not buried in
    `_judged`;
  - `allowed_actions` ← each tool's `needs`;
  - `approval_policy` ← outbox approval (a set of action names; `Reply` waits) (C);
  - `budget {max_steps, max_tokens, max_time}` ← `MAX_READS` / token / timeout;
  - `acceptance_template` ← **new**: the per-type "definition of done".
- `Plan` — the per-**run** artifact ("one", Q3): carries its `TaskContract`
  (inherited) plus the instance-level `objective`, resolved `acceptance`, and
  `steps` / `on_obstacle`.

**`Acceptance`** = `{name, check: "code"|"agent", description}` — `check` names
who verifies (ticket 03): deterministic code vs the calibrated verifier agent.

**Lands now (B):** just the `TaskContract` (type-level) + `acceptance_template`,
and naming the existing deterministic grounding gate as `Acceptance(check="code")`.
`Plan.objective` / model-proposed acceptance / `Plan.steps` / `on_obstacle` all
wait for durable-spine's `Planner` — today the graph *is* the steps.

**Deferred richer `approval_policy`** (auto-approve by provenance) to
durable-spine §8's candidate gate.

New terms `TaskContract`, `Plan`, `Acceptance` go to `CONTEXT.md` § Vocabulary
at build time (not now — decided, not built).
