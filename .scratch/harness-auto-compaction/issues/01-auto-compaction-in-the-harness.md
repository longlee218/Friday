Status: needs-triage
Blocked by:

# Auto compaction in the Harness

Decisions: [The budget in three groups](../../domains-plug-in/issues/17-the-budget-in-three-groups.md).
Research: [research-ideal-context-size.md](../research-ideal-context-size.md).

## Goal

A `Harness` run whose last request's input reached `COMPACT_AT` sends its next
request with a compacted history, so a long diagnose loop stays under ~100K
input tokens per request without losing the tool call/return pairing.

## Seam

`friday/kernel/harness/harness.py:392`, the `Agent(...)` construction: add
`capabilities=[ProcessHistory(compact)]` (Pydantic AI 2.46.0,
`pydantic_ai.capabilities.ProcessHistory`; `history_processors=` no longer
exists). `compact(ctx, messages) -> messages` lives in its own module,
`friday/kernel/harness/compaction.py`, as a plain function.

## Measuring the size

The provider-reported `input_tokens` of the latest `ModelResponse` in
`messages` — no extra call. `ctx.context_window_used` is `None` here: the model
is built with `OpenAIProvider(openai_client=…)` (`harness.py:880`), so Pydantic
AI does not know the window, and ticket 17 decided not to depend on it. The
request that crossed the line has already been sent; acceptable, since 100K is
far under the 1M window.

## Acceptance

- [ ] Under `COMPACT_AT`: history passes through unchanged (same objects).
- [ ] At/over `COMPACT_AT`: every tool return except the last
      `KEEP_TOOL_RESULTS` has its content replaced by a short marker naming
      the tool; every tool call still has its paired return; the system /
      first user prompt and the latest turn are untouched.
- [ ] Still ≥ `SUMMARISE_AT` after stage 1: stage 2 runs (see Open).
- [ ] A compaction is recorded (log line + what the board already reads for a
      run), so an operator can see a run was compacted.
- [ ] Tests drive a scripted model through a loop past the threshold
      (no network); whole suite green; the guard deleted once and watched red.

## Open (decide before `ready-for-agent`)

- Stage 2: which model writes the summary, what it keeps, and what happens if
  it fails.
