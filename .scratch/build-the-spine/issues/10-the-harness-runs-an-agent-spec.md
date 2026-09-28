Status: done
Blocked by: 05

# The Harness runs an `AgentSpec`

Decisions: [The plugin API surface](../../domains-plug-in/issues/03-the-plugin-api-surface.md) §3–4,
[The runner](../../domains-plug-in/issues/13-the-runner-and-adaptive-replan.md) §1,
[Re-triage](../../domains-plug-in/issues/16-hand-off-between-actions-by-re-triage.md) §1,
[The budget](../../domains-plug-in/issues/17-the-budget-in-three-groups.md),
[Pause/resume](../../domains-plug-in/issues/14-the-durable-spine-workflow-and-pause-resume.md) §4.

## Goal

One core entry that runs any declared agent:
`run_agent(spec, tier, toolsets, context, brief, history=None) -> result |
Ask | HandOver | Replan | Retriage`.

- Toolsets = contract ∩ `spec.toolsets`, built per run from `RunContext`.
- Core terminal tools on every agent: `ask_reporter`, `hand_over`,
  `replan(reason, found)`, `retriage(reason, found)`; `ask_reporter` dropped
  when the contract has no `ask`.
- Budget → `UsageLimits(request_limit=max_turns, total_tokens_limit=tokens)`.
- An `Ask` carries the agent's `message_history` + `Evidence`; passing
  `history` continues from it with the reply appended (reads not repeated,
  `Lnn` stable).
- Only `harness.py` imports `pydantic_ai`.

## Acceptance

- [x] Each outcome reachable by a scripted transport (tests).
- [x] Continuing from a stored `Ask` keeps `Lnn` ids and makes no repeat
      read (test — carried from build-the-loop ticket 04).
- [x] Over `max_turns` / `tokens` stops the run (tests).
- [x] `CONTEXT.md`: *terminal tool*, *continuation point*.
- [x] Whole suite green; `code-review` done.

## Decided while building (operator, 2026-09-29)

- A run stopped by its budget returns `HandOver("budget_spent: …")`; any other
  run without a result raises `AgentRunFailed` (the runner, ticket 12, counts
  it as a failed step).
- The signature gains `contract`:
  `run_agent(spec, tier, contract, toolsets, context, brief, history=None)`.
  `toolsets` are the registered `ToolsetSpec`s; `history` is the stored `Ask`
  and, when continuing, `brief` is the reporter's reply.

## Carried

- **To 12/14:** `Ask.evidence` is the live `Evidence` object, not JSON;
  storing a continuation point in `step_results` needs its serialized form.
  `Ask.history` is already JSON (tested). `Replan`/`Retriage` are not yet in
  the `Outcome` union.
- **Kept as they were, both documented harness rules:** `request_limit` is
  `max_turns + OUTPUT_CORRECTIONS` (a correction is an attempt, not a turn),
  and `tokens` is counted per provider attempt (harness comment, ticket 01).
- `AgentSpec` has no `request_timeout_seconds`; a `timeout` in the tier's
  `settings` is the only bound on a request for a spec-run agent.
- A budget-stopped run whose prose held a non-fitting object gets a
  `budget_spent:` reason whose tail names the fit failure instead of the
  budget. The branch taken is right; only the text is off.
