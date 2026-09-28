# Harness auto compaction

## Why

An agent loop (diagnose reads logs and code) re-sends its whole history on
every turn. Nothing in the Harness measures that history, so it grows until a
turn or token limit, or the provider, stops the run. Quality drops long before
the model's 1M window: see `research-ideal-context-size.md` (Chroma "Context
Rot", RULER, Anthropic's context editing: +29% and −84% tokens from clearing
old tool results).

## What

The Harness compacts a run's message history when one request's input
crosses a fixed threshold. Two stages, both core constants:

1. `COMPACT_AT` ≈ 100K input tokens → clear the content of old tool returns,
   keeping the last `KEEP_TOOL_RESULTS` = 3; every tool call keeps its paired
   return (content replaced by a marker), so the history stays valid.
2. Still ≥ `SUMMARISE_AT` ≈ 150K → summarise the older part of the history.

The threshold is **not a budget** and not a % of the context window
(`domains-plug-in` ticket 17: the budget is `(max_turns, tokens)` only).
Numbers are hypotheses until the diagnose eval can score (memory
`build-the-loop-scoring-deferred`): then compare 50K / 100K / 200K.

## Ground rules

- Surgical: the Harness only; no config knob (constants beside their user,
  `domains-plug-in` ticket 07).
- Whole suite green; a guard added is deleted once and watched go red
  (`CLAUDE.md` § Verifying a change).

## Tickets

- [01 — Auto compaction in the Harness](issues/01-auto-compaction-in-the-harness.md)
