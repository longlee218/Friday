Status: done
Blocked by:

# Knobs to constants, budget in three groups

Decisions: [Where the core's behaviour knobs live](../../domains-plug-in/issues/07-where-core-behaviour-knobs-live.md)
(as amended by 17), [The budget in three groups](../../domains-plug-in/issues/17-the-budget-in-three-groups.md).

## Goal

`config.yaml` holds provider keys, named model tiers and install facts only;
every other knob becomes a named constant beside its user; time budgets go.

- Install facts kept: `database_path`, `operator_id`, `board_host/port/origins`,
  `repo_root`, `mcp_servers`, `watched_channels`, `backup_dir`,
  `skills_directory`, `sensitive_words`, `ingest.mention_types`,
  `workflows.concurrency`. (`ssh_host`, `loki_server/tool` become constants
  in 09 — ticket 03 amend §2; keep them here until then.)
- Knobs → constants (table in ticket 07 §4): triage `confidence_threshold`,
  `max_message_age`, `examples`; responder `tone_examples`; ingest, room
  summary, ops constants.
- Attempts → core constants: `PROVIDER_ATTEMPTS 3`, `OUTPUT_CORRECTIONS 1`,
  `STEP_ATTEMPTS 2`, `OUTBOX_ATTEMPTS 3` (backoffs beside them).
- Deleted: `daily_token_budget`, `devops.timeout_seconds`,
  `AgentConfig.timeout_seconds`, `AgentConfig.context_window`,
  `node.timeout_seconds`, `check_node_clocks`, `check_graph_clocks`,
  `+ tool_turns + extra_turns` in `harness.py`. (`max_asks`,
  `auto_ask_for_details`, `use_responder`, `extraction_budget_tokens` go
  with the extractor in 16.)
- Per-agent budget = `(max_turns, tokens)` → `UsageLimits(request_limit,
  total_tokens_limit)`; `max_turns` includes tool turns.
- A per-tool-call timeout constant on SSH / MCP / git reads.
- `agents:` block becomes named tiers; an undeclared tier refuses the boot.

## Acceptance

- [x] `config.yaml` / `config.example.yaml` hold only keys, tiers, install facts.
      (`config.example.yaml` does not exist — n/a. Still read until 13/16:
      `triage_examples`, `workflows.max_asks/use_responder/auto_ask_for_details`,
      `context.extraction_budget_tokens`.)
- [x] Each moved knob is a named constant pinned by a test.
- [x] No time budget left (grep: `timeout_seconds`, `check_*_clocks`,
      `context_window`, `daily_token_budget` — none outside history).
- [x] A hung tool call is cut by the per-call timeout (test).
- [x] `max_turns` counts tool turns (test).
- [x] `CONTEXT.md` § Vocabulary: *install fact*, *knob*, *turn*, *attempt*.
      (*turn* already meant a person's messages; the model sense is *agent turn*.)
- [x] Whole suite green; `code-review` done; guard watched red.
