Type: grilling
Status: resolved
Blocked by:

# Where the core's behaviour knobs live

## Question

`config.yaml` now holds only provider keys and named model tiers. Today it also
holds behaviour knobs: triage's `confidence_threshold`, `max_message_age`,
`examples` count, `workflows.auto_ask_for_details` (goes with the extractor), and
per-agent timeouts. Decide where each goes — a code default, a board setting the
operator changes at run time, or a narrow `config.yaml` section kept on purpose —
and which simply disappear.

## Answer

Decided 2026-09-28 (grilling).

A **knob** is a value that tunes how Friday behaves and is the same on every
machine; an **install fact** describes this machine (who the operator is,
where the db lives, which servers and channels). Only install facts stay in
the file.

1. **`config.yaml` = provider keys + named model tiers + install facts.**
   Install facts: `database_path`, `operator_id`, `board_host/port/origins`,
   `repo_root`, `mcp_servers`, `watched_channels`, `ssh_host`,
   `loki_server/tool`, `backup_dir`, `skills_directory`, `sensitive_words`,
   plus `ingest.mention_types` and `workflows.concurrency` (both depend on
   this install: the account's roles, the machine). **Amends the map's
   "config.yaml holds only provider keys and named model tiers."**
2. **No board settings.** No settings table, migration or UI. Returns only
   when a real runtime need shows up (the first time spend must be capped
   live).
3. **Every other knob is a named constant next to the code that uses it**,
   pinned by a test; changing it is a commit. `confidence_threshold` in
   particular changes only with a `run_triage_eval` threshold table beside it.
4. **Where each knob goes:**

| Knob | Goes to |
| --- | --- |
| triage `confidence_threshold`, `max_message_age`, `examples` | core triage constants |
| responder `tone_examples` | responder constant |
| per-agent `timeout_seconds`, `max_turns`, `temperature` | the **agent declaration** (core agents in core, domain agents in their plugin); temperature is per job, not per tier |
| `max_attempts`, `retry_backoff_seconds` | model-layer constants |
| `outbox.max_attempts`, `backoff_seconds` | outbox constants |
| ingest `turn_seconds`, `sweep_interval_seconds`, `context_messages` | ingest constants |
| `context.summary_max_chars` | room-summary constant |
| `heartbeat_seconds`, `down_after_seconds`, `summary_at_hour`, `keep_model_calls_days`, `keep_backups` | ops constants |
| `daily_token_budget` | **deleted** (never set; heartbeat reports spend) |
| `devops.timeout_seconds` | **deleted** → the contract's `total_time` (ticket 01) |
| `workflows.max_asks` | **deleted** → `max_replans` counts reply-driven replans (ticket 13) |
| `workflows.auto_ask_for_details`, `use_responder` | **deleted** with the extractor; how an `Ask` is worded is the pause/resume ticket's (14) |
| `context.extraction_budget_tokens` | **deleted** with the extractor |
| `triage_examples` | already deleted (ticket 02) |

Consequence: `friday/kernel/config.py` loses `WorkflowConfig`, the outbox /
ingest / ops sections and every per-agent behaviour field; the `agents:` block
becomes tiers. New term for `CONTEXT.md` § Vocabulary at build time:
*install fact* (and *knob* if the build keeps using it).

## Amended 2026-09-28 by ticket 16

Per-agent `timeout_seconds` is deleted, not moved to the agent declaration; `devops.timeout_seconds` is deleted without becoming `total_time`; `context_window` is deleted. Time lives only as a per-tool-call timeout constant. See [The budget in three groups](16-the-budget-in-three-groups.md).
