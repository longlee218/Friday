# The four techniques, in depth

## 1. Compaction

Summarize a conversation nearing its context limit and reinitiate with the summary in place of the full history. The tuning axis is recall-then-precision: start by over-capturing so nothing load-bearing is lost, then trim from there — not the other way around. The cheapest possible first move, before reaching for a model-authored summary at all: clear tool-call results once they've served their purpose, since a tool's raw output is usually only needed for the step immediately after it. Claude Code's own implementation additionally keeps recently-accessed files alongside the compressed summary, on the theory that "what was just touched" is disproportionately likely to matter next.

## 2. Structured note-taking (agentic memory)

The agent periodically writes notes to storage *outside* the context window and reads them back later — persistent memory with minimal per-turn overhead across resets. The cited example: Claude playing Pokémon, tracking exact step counts and strategy across thousands of steps by consulting its own notes rather than holding everything in-window. Anthropic ships a public-beta memory tool on the Claude Developer Platform for this: a `/memories` file directory with client-executed `view`/`create`/`str_replace`/`insert`/`delete`/`rename` commands. The tool's own documentation frames it explicitly as supporting "just-in-time context retrieval" — the two techniques are meant to compose, not compete.

Two related, separable primitives worth knowing about if you're building on the Claude Developer Platform directly:
- **Context editing** clears specific tool results client-side, mid-conversation.
- **Compaction** summarizes the whole conversation server-side when nearing the limit.

They're designed to be combined for long-running agents: compaction keeps the active context small; the memory tool preserves what must survive a summarization pass that would otherwise discard it.

## 3. Sub-agent / multi-agent architectures

Instead of one agent carrying all state for an entire task, specialized sub-agents do deep work in their own clean context windows and return only a condensed summary (cited figure: 1,000–2,000 tokens) to a lead agent that plans and synthesizes. This isolates detailed search/exploration context inside the sub-agent so the lead's window stays focused on synthesis rather than filling up with intermediate work nobody upstream needs to see. Anthropic's own production case study ("How we built our multi-agent research system") reports roughly a 90% improvement over a single-agent baseline on its internal research eval — at roughly 15x the token cost of a single chat turn. That ratio is the actual trade being made: sub-agent isolation buys quality and reach, and it costs tokens proportional to how many isolated contexts you spin up. An early failure mode worth avoiding on purpose: the lead agent spawning 50 sub-agents for a query simple enough to need one.

## 4. Just-in-time retrieval vs. pre-loading

Rather than embedding-based retrieval that stuffs relevant documents into context before inference, an agent keeps lightweight identifiers (file paths, stored queries, links) and loads the real data at runtime via tools. Claude Code's own example: writing a targeted DB query, or using `head`/`tail` over a large output, instead of loading a full data object up front. The framing is deliberately about mirroring human cognition — an external index (a file system, a bookmark) rather than memorizing an entire corpus — and it enables progressive disclosure for free: a file's own path (`tests/test_utils.py` vs. `src/core_logic/test_utils.py`) already carries signal the model doesn't have to be told explicitly.

Named trade-off: runtime exploration is slower and needs deliberately opinionated engineering to stop an agent wasting context wandering down dead ends — just-in-time retrieval is not automatically better than pre-loading, it's a different bet. The recommended default is hybrid, and Claude Code's own architecture is the worked example: pre-load what's cheap and stable (its `CLAUDE.md`), explore just-in-time for everything else (`glob`/`grep` instead of a pre-built, potentially-stale index).

## A worked check for "is this actually just-in-time, or pre-loading with extra steps?"

Ask: **at runtime, can the model itself decide to fetch more** — a different query, a different file, more of the transcript — **or was everything it's going to see already decided before the call was made?** If the answer is the latter, you've built pre-loading, however small or well-chosen the loaded set is. That's not a failure — pre-loading well-scoped, stable content is exactly what buys prompt-cache stability across repeated calls — but naming it accurately matters, because the two techniques solve different problems: pre-loading optimizes for a *known, bounded* need; just-in-time retrieval optimizes for a need that can't be fully anticipated ahead of time.
