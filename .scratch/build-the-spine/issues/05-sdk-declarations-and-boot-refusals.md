Status: done
Blocked by: 03

# SDK declarations and boot refusals

Decisions: [The plugin API surface](../../domains-plug-in/issues/03-the-plugin-api-surface.md)
(incl. its amendment), [The action contract](../../domains-plug-in/issues/01-is-the-action-spec-the-task-contract.md),
[Recognition](../../domains-plug-in/issues/02-the-recognition-reasoning-and-the-assembled-triage-prompt.md) §1, §5,
[The Planner](../../domains-plug-in/issues/12-the-planner.md) §2–3.
Stub: `plugin_api_surface_STUB.py` on `prototype/plugin-api-surface`.

## Goal

The new registration surface, pure data, registered beside the old
`TaskTypeSpec` until 16.

- `friday/sdk/plugin.py`: `Plugin(id, register, enricher=None)`; `register`
  runs once. `Plugin.config`, `Plugin.requires`, `deps`, `caps` do not exist
  on the new surface.
- `friday/sdk/action.py`: `Action(name, recognition, contract,
  acknowledge=None, planning=None)`; `Recognition(means, pick_when, not_when,
  examples)`; `ActionContract(allowed_step_types, allowed_agents,
  allowed_toolsets, constraints, approval_policy, acceptance_template,
  limits(max_replans, max_steps))`.
- `friday/sdk/agent.py`: `AgentSpec(name, description, instructions, result,
  tier, toolsets, budget=(max_turns, tokens), temperature)`.
- `friday/sdk/toolset.py`: `ToolsetSpec(name, description, factory,
  mcp={server: TOOLS})`; `RunContext(task_id, domain, evidence, mcp)`.
- `api.action`, `api.agent`, `api.toolset`; `api.memory_kind` / `api.reader`
  unchanged. Two-lifecycle `plugin_host.py` becomes one.
- Boot refusals (offline): 1 duplicate name; 2 name not under
  `<plugin.id>.` / `core.`; 3 tier not in config; 4 dangling reference
  (agent→toolset, contract→agent/toolset, reader→agent except `"code"`);
  5 domain-type mismatch; 6 `mcp` server not in config; 7 recognition checks
  (dangling/self `not_when`, shared example, no examples, empty `means` /
  `pick_when`); 8 cross-plugin grant; 9 `core.shell` host not declared;
  plus a missing `description` on an agent or toolset.

## Acceptance

- [ ] One test per refusal, each watched red. — 1–8 and the missing
      description: done, each watched red (`tests/test_boot_refusals.py`).
      **9 not done**: moved to ticket 08 by the operator (2026-09-28), since
      `core.shell` and the config host list land there.
- [x] `sdk` still imports nothing of ours but itself (dependency-rule test).
- [x] `CONTEXT.md` § Vocabulary: *domain*, *action*, *action contract*,
      *agent spec*, *toolset spec*, *run context*, *enricher*.
- [x] Whole suite green; `code-review` done.

## Decided while building (operator, 2026-09-28)

- `Plugin.config` stays until ticket 09 (the DAG path's backend config);
  `Plugin.requires` is gone.
- The reader → agent check runs only for a plugin that has registered at
  least one `AgentSpec` (backend's `backend.diagnose` reader has none yet);
  temporary until 16.
- `ToolsetSpec.domain_type` added (refusal 5 needs it; from the stub).
- A config without `tiers` / `mcp_servers` (the memory-kinds default) skips
  those two checks.
