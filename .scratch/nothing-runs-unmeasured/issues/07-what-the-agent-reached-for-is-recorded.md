# 07: What the agent reached for is recorded

**What to build:** Tool calls are hooked and recorded — name, arguments,
outcome, duration — alongside the model calls that made them.

**Blocked by:** 01, 02

**Decisions:** D1, D3

**Status:** done

## Why

`LogHooks` implements `on_llm_start` and `on_llm_end` and nothing else. The SDK
offers `on_tool_start` and `on_tool_end` and neither is used, so nothing
records that an agent called `search_skills`, what it searched for, which skill
came back, or how many bytes of a skill body entered the next prompt.

Four of the seven tools reach a skill, and the split between them is by what
the agent already knows — `fetch_skill` when it has the name, `search_skills`
when it does not. Whether that split is working is an empirical question about
which tool agents actually reach for, and there is no data to answer it with.

It matters more the day an MCP server is switched on. `mcp_servers` is empty
today, with `loki` and `source` commented out and ready. A tool that reaches
outside this process, whose arguments a model chose, with no record of what it
was asked for, is a blind spot in the one place a blind spot is expensive.

## Most of this already exists — do not build it twice

Added after reading the SDK on 2026-09-05. Three things this ticket was about
to invent are already there:

**The identity of a call is in the context object.** The runtime passes every
tool an `agents.tool_context.ToolContext` — a subclass of `RunContextWrapper`
carrying `tool_name`, `tool_call_id`, `tool_arguments`, `tool_call`,
`tool_namespace`, `agent` and `run_config`. `friday/agent/harness.py` currently
aliases that *name* to the parent class, so the fields are there at runtime and
invisible in the types. Ticket 10 fixes the alias; this ticket should read the
fields rather than reconstruct them from hook arguments.

**Interception is a supported position, not just observation.**
`tool_input_guardrails` and `tool_output_guardrails` run before and after a
function tool and see its arguments. `needs_approval` can be a callable over
`(run_context, tool_parameters, call_id)`, so approval can depend on what is
actually being asked for. That is the argument-level guard this board's
original review said did not exist, and it exists for function tools.

**It does not exist for MCP tools, and the difference decides a design.**
`agents/mcp/util.py:577` builds a `FunctionTool` for each MCP tool with
`needs_approval` and `strict_json_schema` and no guardrails; the server's own
`require_approval` policy callable is handed `(run_context, agent, tool)` —
never the arguments (`agents/mcp/server.py:828`). So an MCP tool can be gated
by name, or by a human, but not automatically by what it was asked to do. The
day `loki` and `source` are switched on, the way to bound a query range or a
file path is a function tool of ours wrapping the MCP call — not the `allow`
list, which only filters names.

## Acceptance criteria

- [x] `LogHooks` records tool starts and ends through the same sink as model
      calls, keyed to the call and the task
- [x] The call's identity comes off the context object the SDK already
      passes, not from a mechanism built here
- [x] Arguments are scrubbed on the way in — `friday/ops/redact.py`, same as
      every other stored string
- [x] A tool that raises is recorded as such, not lost — including the ones
      the SDK turns into a message for the model rather than an exception
      (ticket 10.1), which are invisible to a hook that only watches for
      raises
- [x] The board shows an agent's tool calls next to the prompt that produced
      them
- [x] Each guard is deleted once and watched go red

## What it came to

`ToolCall` beside `ModelCall`, and `tool_calls` beside `model_calls`: a tool
call has no prompt and no tokens, a model call has no arguments and no result,
and one table would be half nulls and a discriminator nobody reads. One sink
still, for D1's reason — a second seam is a second thing to forget — parting
at the composition root, which is the only place that knows there are two
tables.

**The identity came off the context object, as this ticket said it would.**
`ToolContext` carries `tool_call_id` and `tool_arguments`, which is how a
start is correlated with its end (the hooks are given no other way), and it
carries `agent`, which is how the harness reaches the hooks to mark a failure.
That is the field ticket 10 found the alias had been hiding.

**A failure has to be told rather than observed.** `_tool_failed` turns a
raising tool into a message for the model, so `on_tool_end` sees a perfectly
ordinary result — a hook watching for raises sees nothing, and every failure
would be filed as an answer that happens to read like one. `failed` is a
column because the alternative is reading the result text and guessing.

One thing the work turned up: triage now writes two entries per decision, the
prompt and the `classify` call its answer arrives as, and a test that unpacked
the sink with `(call,) = calls` broke. That is the correct shape — the tool
call is real — and the test now says which of the two it is about.

738 tests pass (735 before, +3). Four guards, each deleted once and watched go
red.
