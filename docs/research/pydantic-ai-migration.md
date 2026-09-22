# Moving the harness from openai-agents to Pydantic AI

Researched 2026-09-22. Two inputs: an inventory of every openai-agents
feature Friday uses (read from the code), and Pydantic AI's current docs
(Context7 + `pydantic.dev/docs/ai`). The unknowns were then run against
the real provider and MCP server the same day — see "Verified" at the end.

## Versions

- **openai-agents 0.22.0** (`pyproject.toml`, `uv.lock`) — a 0.x line with
  no API stability promise.
- **pydantic-ai 2.46.0** (2026-09-19). V2.0.0 shipped 2026-06-23. Policy:
  no intentional breaking changes in minor releases; deprecated features
  go only at the next major, no sooner than 3 months after V2; V1 gets
  security fixes until about 2026-12-23. MIT.
  https://pydantic.dev/docs/ai/project/version-policy/
- **Many snippets online describe V1** (`MCPServerStdio`, `httpx`, the
  `openai:` prefix meaning Chat Completions). V2 changed all three; use V2
  docs only.

## Feature map

| Friday uses today (openai-agents) | Where | Pydantic AI V2 | Effect |
| --- | --- | --- | --- |
| `OpenAIChatCompletionsModel(AsyncOpenAI(base_url, api_key, max_retries=0))` | `harness.py` | `OpenAIChatModel(name, provider=OpenAIProvider(openai_client=AsyncOpenAI(max_retries=0, …)))`; the bare `openai:` prefix now means Responses, so build the class explicitly | Same |
| One `asyncio.wait_for` over the whole run | `harness.py` | `ModelSettings(timeout=)` is per request only | Keep `wait_for` |
| Own retry loop over openai exception types | `harness.py` `_attempts` | Unchanged (client retries off); optional tenacity transport | Same |
| **Structured answers**: a hand-built `answer` `FunctionTool`, `tool_choice="required"`, a custom `tool_use_behavior` returning `ToolsToFinalOutputResult`, `fits()` in `on_invoke_tool`, "that did not fit" string for one correction, instance read from `result.new_items` because `final_output` is stringified | `harness.py`, `structured.py` | `output_type=ToolOutput(Answer)` (a tool call, not `response_format`), `@agent.output_validator` raising `ModelRetry`, `retries={'output': 1}` = exactly one correction; `UnexpectedModelBehavior` when spent; `result.output` is the instance | **Largest simplification** — most of `run_structured`'s machinery goes |
| `max_turns` = config + tool turns + correction | `harness.py` | `UsageLimits(request_limit=…, tool_calls_limit=…)`, `UsageLimitExceeded` | Same, clearer names |
| Context `FridayState` via `Agent[context_type]`; tools take `ctx: ToolContext[FridayState]`, relying on `function_schema` recognising `ToolContext` **by identity** | `harness.py`, `tools/*` | `Agent(deps_type=FridayState)`; tools take `ctx: RunContext[FridayState]`, hidden from the schema by design | Removes an internal-behaviour dependency |
| `tool()` wraps `function_tool` with `failure_error_function` → "unavailable, carry on", except `ModelBehaviorError`; imports private `default_tool_error_function` | `harness.py` | Raise `ToolFailed` in the tool, or a `tool_execute_error` hook whose return value is sent to the model; argument validation errors retry by default | Removes a private import |
| `LogHooks(AgentHooks)`: `on_llm_start/end`, `on_tool_start/end` → `model_calls`/`tool_calls` rows; **`agent.hooks` reassigned before every run** | `llm_log.py`, `harness.py` | `Hooks` capability: `before/after_model_request`, `before/after_tool_execute`, `tool_execute_error`, `run_error`; per-call `RequestUsage` on each response; run identity read from `ctx.deps` | Removes per-run mutation of a shared agent |
| Usage from `response.usage.input/output_tokens`; budget checked by a `spent` callback | `llm_log.py`, `harness.py` | `RequestUsage` per call, `RunUsage` per run (a shared `RunUsage` can span runs); token and cost limits in `UsageLimits` | Same; optional per-run token cap |
| Tracing disabled twice | `harness.py` | No tracing unless instrumented; `opentelemetry-api` is a no-op dependency | Same |
| MCP: `MCPServerStdio/Sse/StreamableHttp`, `create_static_tool_filter`, `cache_tools_list`, `params["auth"]` = Friday's `SsoTokens(httpx2.Auth)` | `mcp.py`, `auth.py` | One `MCPToolset` on FastMCP's client; `StdioTransport(command, args, env, cwd)`, `SSETransport`, `StreamableHttpTransport`; `auth=` accepts an `httpx2.Auth`; allow-list via `.filtered()`; `tool_error_behavior` | Rewrite of `mcp.py`; `SsoTokens` already targets `httpx2` |
| `RunState` checkpoint/resume, interruptions, `approve()` | `harness.py` | Deferred tools: `DeferredToolRequests` / `DeferredToolResults`, resume from serialized `message_history` | **Nothing calls it today** — delete, or map when HITL returns |
| Tests: `agents.testing.ScriptedModel`, `function_call`, `assistant_message` (~40 imports), `agents.models.interface.Model`, `ModelResponse`, `Usage`, `Converter.tool_to_openai`, `get_trace_provider` — in 13 test files | `tests/` | `FunctionModel(fn(messages, info) -> ModelResponse)`, `TestModel`, `agent.override(model=…)`, `ALLOW_MODEL_REQUESTS = False`, `capture_run_messages()` | **Largest cost** |

### Internal SDK behaviour Friday relies on today (all go away)

`function_schema` identity check on `ToolContext`; `final_output`
stringified without `output_type`; a raise in `on_invoke_tool` becoming
`UserError`; `reset_tool_choice=True` default; the private
`agents.tool.default_tool_error_function`; `max_turns` baked into
`RunState`; mutating `agent.hooks` per run.

## Dependencies

`pydantic-ai-slim[openai,mcp]`: `pydantic`, `pydantic-graph`, `httpx2`,
`anyio`, `opentelemetry-api`, `genai-prices`, `griffelib`,
`typing-inspection`, plus `openai`, `tiktoken`, `fastmcp-slim[client]`.
Roughly a like-for-like swap for `openai-agents`; `fastmcp` and `tiktoken`
are new, `httpx2` sits beside the `httpx` that `openai` uses. Never the
full `pydantic-ai` package (it pulls every provider, Logfire, CLI, evals).

The standing rule survives: one module imports the vendor
(`friday/agent/harness.py`), everything else takes names through it.

## Why it is worth doing

1. Seven dependencies on SDK internals disappear (above).
2. Structured output — the thing every agent here does — becomes the
   framework's main path instead of a hand-built tool plus a custom stop
   rule.
3. Typed dependencies (`RunContext[FridayState]`) and hook capabilities
   replace an identity trick and per-run mutation.
4. A stability policy instead of a 0.x line.
5. Other vendors (Anthropic, Gemini) are built in, which makes
   DESIGN-v2 §6.7's "ModelProvider port" mostly unnecessary: the framework
   owns the loop and the providers. Jev (`docs/research/jev-decision-models.md`)
   also ships a Pydantic AI integration.

## Risks

- **Tool forcing on the configured provider** — verified: MiniMax-M3
  receives `tool_choice='required'` and honours it. Keep `str` out of
  `output_type`, or text becomes an acceptable answer.
- **Tests.** 13 files import the SDK directly; the two harness-focused
  files hold 86 tests.
- **Prompts change shape.** Tool and output schemas are rendered by a
  different library, which is upstream of the triage prompt: `CLAUDE.md`
  rule 4 applies — run `evals/run_triage_eval` and report.
- **MCP on FastMCP** is new code between Friday and a Keycloak-protected
  server. The catalogue read and per-request auth are verified against the
  real server; a real tool call through it is not yet.
- **V2 is three months old** and releases every few days. Pin exactly.
- **Semantics to re-check:** V2's `end_strategy='graceful'` runs function
  tools called in the same turn as the output tool; retry counters are per
  tool, not shared.

## Durable execution: DBOS, not Temporal (operator, 2026-09-22)

Pydantic AI integrates both (`DBOSDurability`, `TemporalDurability`).
**DBOS** runs in-process as a library with its state in SQLite or
Postgres; **Temporal** needs a server and a worker beside Friday. Friday
runs on one machine, one process, SQLite, so DBOS is the choice and
would replace the hand-written DAG engine (`friday/dag/`): workflows and
steps for graphs and nodes, a queue for the pool's concurrency, `recv`
with a timeout for `Ask`/`HandOver` waits, scheduled workflows for the
summariser and liveness. Friday keeps the envelope, the `node_runs` rows
and the task state machine. Integration rules to design for: `deps` must
serialise (ids, not live handles); capabilities are attached at
construction, never per run; custom tools are not wrapped as steps
automatically (safe while every tool is read-only). Temporal only when
Friday runs on more than one machine. **Not yet spiked**: DBOS's system
database beside `friday.db` (or in it), `DBOSDurability` inside a step,
`recv` with a timeout, queue concurrency, in-flight workflows across an
application-version change, DBOS under pytest.
https://pydantic.dev/docs/ai/capabilities/durable_execution/dbos/ ·
https://pydantic.dev/docs/ai/capabilities/durable_execution/temporal/

This work is scheduled **after the DESIGN-v2 restructure** (roadmap,
`CONTEXT.md` § Project state), not inside it.

## Suggested order

After the DESIGN-v2 restructure and `api_issue` v4 (roadmap items 2 and 4; this is item 5), behind the seams they
leave.

1. ~~**Spike**~~ — done 2026-09-22, 15/15 (see "Verified").
2. **Friday-owned test doubles** (`ScriptedModel`, `function_call`,
   `assistant_message` under `friday/sdk/testing/`, as DESIGN-v2 §13
   already plans), implemented on today's SDK; move the 13 test files onto
   them. Suite green, no behaviour change. After this, tests stop importing
   any vendor.
3. **Swap `harness.py` internals** behind the same `Harness` API (`run`,
   `run_structured`, `tool`, `ToolContext` becoming an alias of
   `RunContext`); re-point the test doubles at `FunctionModel`. The suite
   should not change.
4. **Swap `mcp.py`** to `MCPToolset`; prove auth against the real server.
5. **Delete the unused checkpoint/resume path.**
6. **Measure:** triage eval (accuracy, confusion matrix, thresholds) and a
   captured-case replay (`replay_case.py --diagnose`), reported with the
   change.

## Verified — spike, 2026-09-22

A throwaway script (not in the repo) ran `pydantic-ai-slim[openai,mcp]==2.46.0`
in an overlay environment (`uv run --with`, so `pyproject.toml` and
`uv.lock` untouched) against the configured provider, **MiniMax-M3 at
`https://api.minimax.io/v1`**, and the real `devops-generic` MCP server.
The request payloads were captured with an `httpx2` event hook. All 15
checks passed; one of them (the correction budget) misbehaved once in 15
repetitions, noted in its row.

| Check | Result |
| --- | --- |
| Tool forcing on MiniMax | `tool_choice='required'` sent, only the `answer` tool offered, **no `response_format`** — the default profile assumes forcing is supported, and MiniMax honours it |
| `ToolOutput` structured answer | `result.output` is the dataclass instance, one request |
| One correction, then accepted | `retries={'output': 1}` + `ModelRetry` in an output validator: validator ran twice, two requests |
| One correction, then stop | Always rejecting: `UnexpectedModelBehavior("Exceeded maximum output retries (1)")` after two requests. **14 of 15 runs** behaved exactly so; once the second response never reached the validator (spent on something else — not reproduced in 14 further runs, cause not captured). The budget is shared between argument-validation failures and validator rejections, which matches v1's single correction |
| `RunContext[FridayState]` hidden from tool schema | The schema sent held only the tool's own parameters |
| Hook records every model request | `after_model_request` fired once per HTTP request, with per-call `input_tokens`/`output_tokens` and `ctx.deps.task_id` |
| Tool failure → "unavailable, carry on" | `tool_execute_error` hook returning a string: the model saw it and finished the run. **Note:** `after_tool_execute` then also fires with the substituted result, so the recorder must not log that call twice |
| `result.usage` | A property (`RunUsage`), not a method |
| `UsageLimits` | `UsageLimitExceeded` raised before the request |
| Whole-run timeout | `asyncio.wait_for` cancels cleanly; the same agent's next run succeeds |
| Capabilities per run | `agent.run(..., capabilities=[...])` works |
| `FunctionModel` scripted transport | Offline, deterministic |
| `MCPToolset` + Friday's `SsoTokens` (`httpx2.Auth`) | Real server: 49 tools listed; the auth flow ran on each of the 6 HTTP requests; the refreshed token was written back to `data/credentials/devops-generic.json` as `TokenStore` does |
| MCP allow-list | `.filtered()` offered the model exactly `loki_query_range`, `loki_series` out of 49 |

**Coexistence:** openai-agents 0.22 accepts `openai>=3.0,<4` and `mcp<3`;
pydantic-ai-slim needs `openai>=3.8` (the project has 3.6.0) and brings
`fastmcp-slim` with `mcp` 2.x. Both import in one environment, so the
migration can run with the two libraries installed side by side.

**Found on the way:**

- Pydantic AI prints a Logfire banner to stdout at first use unless
  `PYDANTIC_AI_NO_BANNER=1`; set it in the environment Friday runs in.
  Observability stays off unless configured.
- MiniMax-M3 returns its reasoning as a `ThinkingPart` followed by a stray
  `TextPart` holding `"</think>"`. Harmless while the output is a tool call;
  worth a line in the harness if text output is ever allowed.

Still not checked: a real MCP **tool call** through `MCPToolset` (only the
catalogue was read), and the triage eval under the new harness — that is
step 6 of the order above and needs the swap itself.

Sources: https://pydantic.dev/docs/ai/core-concepts/output/ ·
https://pydantic.dev/docs/ai/core-concepts/hooks/ ·
https://pydantic.dev/docs/ai/core-concepts/agent/ ·
https://pydantic.dev/docs/ai/tools-toolsets/tools-advanced/ ·
https://pydantic.dev/docs/ai/mcp/client/ ·
https://pydantic.dev/docs/ai/project/changelog/ ·
https://pydantic.dev/docs/ai/project/version-policy/ ·
https://pydantic.dev/articles/pydantic-ai-v2 ·
https://pypi.org/pypi/pydantic-ai/json
