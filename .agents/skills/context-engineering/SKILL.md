---
name: context-engineering
description: Manage what actually enters an LLM's context window as it grows across a long or multi-step task — context rot, system-prompt "right altitude," and the four techniques (compaction, structured note-taking, sub-agent isolation, just-in-time retrieval vs. pre-loading). Use this whenever a prompt or agent's context is growing unbounded, when deciding what a system prompt should and shouldn't spell out, when shaping what a tool returns, when a task needs its history trimmed or summarized, or when reviewing a design like Friday's LightContext/FullContext split or its memory-injection-vs-tool-call choice for whether it's actually pre-loading, just-in-time retrieval, or neither.
---

# Effective Context Engineering

Anthropic's engineering-blog treatment of context management for agents (`anthropic.com/engineering/effective-context-engineering-for-ai-agents`). Context engineering is the broader, iterative discipline prompt engineering sits inside: as an agent loops and accumulates tool results, retrieved documents, and its own prior turns, something has to keep deciding which slice of that growing universe actually enters the window on the *next* call. The umbrella principle for every technique below: find **the smallest set of high-signal tokens that maximizes the likelihood of the outcome you want** — "minimal" is not a synonym for "short."

## Context rot: why this is a real constraint, not caution

As token count grows, a model's ability to recall and reason over that context degrades — not as a cliff, but as a gradient. The stated mechanism is architectural: transformer attention creates pairwise relationships between every pair of tokens, so those relationships "get stretched thin" as length grows, and models are trained mostly on shorter sequences, leaving fewer specialized parameters for context-wide dependencies. Anthropic's own analogy is a human "attention budget," spent down as more tokens are added. Treat context as a finite resource with diminishing marginal returns — not a container you fill until it's full.

## System-prompt calibration — "the right altitude"

Two named failure modes to check a prompt against:

- **Too low**: hardcoded, brittle logic trying to force one exact behavior. Expensive to maintain, breaks on anything slightly different from what you imagined.
- **Too high**: vague guidance that "falsely assumes shared context" — gives the model no concrete signal to act on.

The target is specific enough to guide behavior effectively, flexible enough to give the model strong heuristics rather than a decision tree. Organize with clear structure (XML tags or Markdown headers — `<background_information>`, `## Tool guidance`) and aim for the *minimal* set of information that fully specifies the expected behavior.

Tool design is part of this, not separate from it: a bloated or ambiguous tool set burns context on the model's indecision, not just on tokens. Anthropic's own test for a tool set: *"if a human engineer can't definitively say which tool should be used in a given situation, an AI agent can't be expected to do better."* Keep few-shot examples to a small set of diverse canonical cases — "pictures worth a thousand words" for the model — rather than an exhaustive enumeration of edge cases.

## The four techniques

| Technique | What it does | Reach for it when |
|---|---|---|
| **Compaction** | Summarize a conversation nearing its limit, reinitiate from the summary | A single long-running session is about to exceed its window and needs to keep going |
| **Structured note-taking** | The agent writes notes to storage *outside* the context window, reads them back later | Persistent memory has to survive a reset, with minimal per-turn overhead |
| **Sub-agent / multi-agent isolation** | A specialized sub-agent does deep work in its own clean window, returns only a condensed summary to the lead | One task's detailed search/exploration context would otherwise crowd out the lead's ability to synthesize |
| **Just-in-time retrieval** | Keep lightweight identifiers (paths, queries, links); fetch the real data at runtime via a tool, instead of pre-loading it | The corpus is large, cheap to query on demand, and mostly irrelevant to any single step |

**Compaction** should over-capture before it trims — preserve architectural decisions, unresolved issues, and implementation details; discard redundant tool output first, since clearing a stale tool result is the cheapest possible cut before reaching for full summarization. The stated risk of doing this wrong: "overly aggressive compaction can result in loss of subtle but critical context whose importance only becomes apparent later" — that's the actual failure mode compaction has to be judged against, not just token count.

**Just-in-time retrieval vs. pre-loading** is genuinely a spectrum, not a binary choice. The recommended default is hybrid: pre-load what's cheap and stable, explore just-in-time for everything else. True just-in-time retrieval means the *model itself* decides at runtime to fetch more — a deterministic function assembling a bounded, well-chosen context ahead of the call is pre-loading done well, not just-in-time retrieval, even if it's small and stable. Don't claim the latter label for the former; they're both good, but they trade off differently (pre-loading buys prompt-cache stability; just-in-time buys the ability to explore beyond what anyone anticipated).

Extended technique detail, the memory-tool and context-editing primitives Anthropic ships for these, and worked examples live in [`references/techniques.md`](references/techniques.md). Full citations: `docs/research/agentic-system-design/context-engineering.md`.

## Applying this to Friday

**Compaction**: `friday/extraction/context.py`'s budget-based compaction drops the *oldest whole messages* once over budget — never summarizes. That's a stricter, cost-motivated subset of the technique above, not a cruder cousin: it matches the "clear stale content first" cheap move, and whole-message truncation structurally can't produce the "critical context lost inside a bad summary" failure mode, because nothing is ever partially rewritten. What it doesn't solve: a single message that itself exceeds the budget — Friday surfaces that as a visible compaction-cooldown state rather than pretending to fix it.

**Pre-loading vs. just-in-time**: `friday/triage/context.py`'s `LightContext` and extraction's `FullContext` are both pre-loaded, not just-in-time, even though `LightContext` is deliberately small and empirically stable (>90% shared prompt-prefix bytes between calls, measured, not assumed) — no Friday agent holds a tool letting it decide at runtime to pull more transcript or memory. That's a fine place to be for a one-shot, one-retry-budget extractor; it stops being fine only if a future task type genuinely needs to explore rather than have everything decided for it ahead of time.

**Sub-agent isolation logic, reused for something else**: Friday's extractor gets domain memory by *prompt injection* while the responder gets it by *tool call* — not because of a sub-agent split, but for the same underlying reason Anthropic gives for isolating sub-agents: a tight turn budget (one call plus one retry) can't afford to spend a turn on a memory search round-trip, so the information has to arrive pre-assembled instead. Recognize this as the same trade-off wearing a different shape before assuming context-isolation guidance only applies to literal sub-agents.
