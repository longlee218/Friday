Status: ready-for-agent
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

- [ ] Each outcome reachable by a scripted transport (tests).
- [ ] Continuing from a stored `Ask` keeps `Lnn` ids and makes no repeat
      read (test — carried from build-the-loop ticket 04).
- [ ] Over `max_turns` / `tokens` stops the run (tests).
- [ ] `CONTEXT.md`: *terminal tool*, *continuation point*.
- [ ] Whole suite green; `code-review` done.
