Type: grilling
Status: resolved
Blocked by:

# The budget in three groups

## Question

The budget is spread over seven knobs on four layers (daily tokens, per-answer
tokens, transcript tokens, agent/node/graph/task time, turns, provider/node/
outbox attempts). Cut it to three groups — tokens, time, turns/attempts — with
one knob per group: is the token budget per run or per day, per kind or summed;
does an agent loop need a time limit at all; and what exactly is a turn versus
an attempt, and where is each used?

## Answer

Decided 2026-09-28 (grilling, one question at a time). Research behind the
compaction numbers: `.scratch/harness-auto-compaction/research-ideal-context-size.md`.

```
AgentSpec.budget = (max_turns, tokens)          the only per-agent budget
  max_turns   every request to the model in one run, tool turns included
              → UsageLimits(request_limit=max_turns)
  tokens      one number, input + output summed over one run
              → UsageLimits(total_tokens_limit=tokens)
time          no budget anywhere; only a timeout on each tool call (core constant)
attempts      core constants: PROVIDER_ATTEMPTS 3 · OUTPUT_CORRECTIONS 1 ·
              STEP_ATTEMPTS 2 · OUTBOX_ATTEMPTS 3
```

1. **Tokens: per run, summed.** "One run" = one run of one agent (Planner,
   diagnose, responder each have their own). One number, input + output
   cumulative over the run, not split by kind. `daily_token_budget` stays
   deleted (ticket 07); `settings.max_tokens` is a provider setting, not a
   budget.
2. **Context size is not a budget.** The compaction trigger is a fixed core
   constant on one request's input (~100K), not a % of `context_window`:
   with a 1M model, 80–90% of the window sits far past where quality drops.
   `AgentConfig.context_window` (no reader) is deleted. Compaction itself is
   board `harness-auto-compaction`.
3. **No time budget.** An agent loop stops on turns or tokens, as Pydantic AI's
   `UsageLimits` does (it has no time field). Deleted: `ActionContract.limits.
   total_time`, `HandOver` `out_of_time`, `AgentConfig.timeout_seconds`,
   `node.timeout_seconds`, `devops.timeout_seconds`, `check_node_clocks`,
   `check_graph_clocks`, GatePlan's time-left check, the Planner's time budget.
   **Time lives only on a tool call**: each tool call (SSH `kubectl`, MCP call,
   git read) has a timeout, a core constant, so a hung read cannot hold a pool
   slot. A model request relies on the OpenAI client's own default timeout —
   the operator's call, knowing that is the one unbounded wait left.
4. **A turn is progress; an attempt is a repeat.** A *turn* = one request to
   the model whose answer (or tool call) the next request builds on. An
   *attempt* = doing the same failed thing again (provider 429/502, an answer
   of the wrong shape, a failed step, a failed send). Only `max_turns` is a
   budget; `+ tool_turns + extra_turns` (`harness.py:487`) goes — the model
   decides how many tools it calls. Attempts are core constants, the same for
   every agent, not in `config.yaml`: `AgentConfig.max_attempts` /
   `retry_backoff_seconds` and `outbox.max_attempts` / `backoff_seconds`
   become constants; `Node.max_attempts` goes with the DAG. `max_replans` and
   `MAX_ASKS_PER_TASK` are not attempts (each is new work) and stay on the
   contract / spine.

**Amends** 01 (budget on the agent; the contract's limits lose total time),
03 (`AgentSpec.budget`), 06 (`answer_question` loses "10 min"), 07 (per-agent
`timeout_seconds` deleted, not moved), 11 (no time check in the gate), 12
(Planner budget), 13 (no `out_of_time`), 14 (no `total_time`). Each carries a
one-line pointer here.

Terms for `CONTEXT.md` § Vocabulary at build time: *turn*, *attempt*.
