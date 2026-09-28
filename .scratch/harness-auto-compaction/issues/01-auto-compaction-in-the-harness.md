Status: ready-for-agent
Blocked by:
Parked: until the `domains-plug-in` build is done (operator, 2026-09-28).

> **When resuming, re-check the seam.** `domains-plug-in` ticket 09 splits
> `harness.py` (`harness / retry / model_client`) and ticket 03 builds agents
> from `AgentSpec`, so `harness.py:392` will have moved. An early, unrequested
> start sits uncommitted in worktree `../friday-agents-harness-auto-compaction`
> (branch `feat/harness-auto-compaction`): 6 compaction tests green, whole
> suite not run, not reviewed — reuse or discard.

# Auto compaction in the Harness

Decisions: [The budget in three groups](../../domains-plug-in/issues/17-the-budget-in-three-groups.md).
Research: [research-ideal-context-size.md](../research-ideal-context-size.md).

## Goal

A `Harness` run in which any request's input reached `COMPACT_AT` sends every
later request with a compacted history, so a long diagnose loop stays under ~100K
input tokens per request without losing the tool call/return pairing.

## Seam

`friday/kernel/harness/harness.py:392`, the `Agent(...)` construction: add
`capabilities=[ProcessHistory(compact)]` (Pydantic AI 2.46.0,
`pydantic_ai.capabilities.ProcessHistory`; `history_processors=` no longer
exists). `compact(messages) -> messages` lives in its own module,
`friday/kernel/harness/compaction.py`, as a plain function (no `ctx`).

## Measuring the size

The **largest** provider-reported `input_tokens` of any `ModelResponse` in
`messages` — no extra call. Largest, not latest: the request after a
compaction is smaller, and reading only its size would bring the cleared
results back on the next request. `ctx.context_window_used` is `None` here: the model
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
- [ ] Once a run has compacted it stays compacted: a later, smaller request
      does not bring the cleared results back.
- [ ] A compaction is logged (input tokens, how many results cleared).
- [ ] Tests drive a scripted model through a loop past the threshold
      (no network); whole suite green; the guard deleted once and watched red.

## Decided 2026-09-28

- **Stage 1 only.** No summarising: if the context is still large after
  clearing, the run stops on its limits — today `request_limit` / the
  provider; the per-run token budget (`domains-plug-in` ticket 17) is
  decided, not built. Summarising waits for data
  showing stage 1 is not enough.
- **`COMPACT_AT = 100_000`** — Anthropic's default for the same mechanism
  (clearing old tool results), the one with quality data (+29%, −84% tokens).
  Uber's 400K ("Running a Software Factory Efficiently at Uber Scale",
  2026-08-27) was considered: a cost/cache default for interactive coding
  harnesses that summarise, with no quality data. Re-measure 100K / 200K / 400K
  once the diagnose eval scores.

Term for `CONTEXT.md` § Vocabulary at build time: *compaction* (clearing old
tool results in a run's history, not the extractor's transcript truncation).
