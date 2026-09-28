---
name: harnesses-long-running-agents
description: Design a harness for agents that work across multiple sessions or a long-running loop — handoff via structured artifacts (progress files, checklists, git commits) instead of relying on in-context memory, context resets versus compaction, and separating the agent that does the work from the agent that judges it. Use this when designing multi-session or resumable agent workflows, task checkpointing, a generator/evaluator split, or when reviewing whether a piece of harness machinery (like Friday's DAG checkpoint/resume or its outbox approval gate) is still justified by a real assumption or has quietly gone stale.
---

# Effective Harnesses for Long-running Agents

Two companion Anthropic engineering posts on harnesses for agents that outlive a single context window: "Effective harnesses for long-running agents" (Justin Young, Nov 2025) and "Harness design for long-running application development" (its follow-up, covering a later planner/generator/evaluator harness).

## The problem: discrete sessions, no memory between them

An agent working across sessions "must work in discrete sessions with no memory of prior work" — like engineers on shifts, where each new engineer arrives with no memory of what happened on the previous one. Two named failure modes this causes if left unaddressed: attempting a whole task in one pass and leaving half-implemented work when context runs out, and prematurely declaring success after seeing only partial progress.

## Handoff via structured artifacts, not memory

The fix isn't a smarter in-session context strategy — it's externalizing state so the *next* session's cold start is short and reliable:

- An init script that restores the environment.
- A progress log the agent reads before doing anything else.
- A granular checklist (e.g. a feature list) the agent may only flip a status flag on — explicitly *not* something it's allowed to edit or delete entries from.
- Durable checkpoints as the actual unit of progress (git commits, in the source's case), each with a descriptive message.

Each new session is told, explicitly, to read the log and the checklist to get up to speed *before* acting — the harness doesn't assume the agent will think to do this on its own.

## Context resets vs. compaction — and why the answer changed

One harness generation used full context resets between work units rather than in-conversation compaction, on the reasoning that "while compaction preserves continuity, it doesn't give the agent a clean slate, which means context anxiety can still persist — a reset provides a clean slate." That choice was **walked back** as models improved: a later generation moved to a more methodical approach and removed the reset/sprint mechanism entirely, relying on the SDK's own automatic compaction instead. The mechanism was explicitly provisional, tied to a specific model capability level — not a fixed architectural truth to copy forward unquestioned.

## Separate the doer from the judge

A generator that's asked to evaluate its own output tends to confidently praise it, even when a human observer would call the quality obviously mediocre. Splitting the evaluator into a separate agent is a strong lever — but not a free one: tuning a standalone evaluator to be properly skeptical is far more tractable than making a generator self-critical, which means the skepticism has to be engineered deliberately into the evaluator's own prompt. Separation alone doesn't produce it.

## The governing principle

Stated directly in the source: **"every component in a harness encodes an assumption about what the model can't do on its own, and those assumptions are worth stress testing, both because they may be incorrect, and because they can quickly go stale as models improve."** The corollary: find the simplest solution possible, and only increase complexity when needed. This is the exact reason the sprint-decomposition mechanism above was later removed — the assumption behind it (models need externally-imposed task chunking) went stale, and the team removed the machinery rather than keeping it "just in case."

Concrete handoff-artifact formats and a worked example live in [`references/handoff-pattern.md`](references/handoff-pattern.md). Full citations: `docs/research/agentic-system-design/harnesses-long-running-agents.md`.

## Applying this to Friday

**What this validates directly**: `friday/dag/engine.py` checkpointing after every node, and discarding state when a task's parameters change, is exactly the handoff-artifact pattern above — a structured handoff carrying prior state and next steps, just automated as a DB row instead of a file an agent writes for its own future self. Friday's version is stronger in one respect the source doesn't need: an explicit invalidation rule (params changed → state is stale), where the source's harness relies on the agent choosing to trust or re-derive what it reads.

**What this source doesn't cover, and why that's not a gap in the research**: neither post discusses per-attempt timeout budgets, retry whitelists, or bounding a single model call — that's a different layer. These posts are about *session-to-session* continuity across hours- or days-long work; Friday's `_settle`/`Harness._attempts` design is about making any *one* call inside a run well-behaved. Don't reach for this source to justify or critique that layer; it's silent on it.

**A live prompt to apply the governing principle**: Friday still carries a tested checkpoint/resume mechanism with no current caller, left over from a retired multi-node investigation graph. Read strictly, "every component encodes an assumption — stress-test it, because it goes stale" is a mild argument to either delete that mechanism now or name the specific future graph that will need it — not to keep it indefinitely on the theory it might be useful someday.

**The doer/judge split, already present**: Friday's outbox approval gate — nothing composes and sends a message in one step without a separate approval predicate — is the same structural move as the generator/evaluator split, and the source's finding (agents over-praise their own work) is a concrete, citable reason that gate matters, beyond "someone should review it."
