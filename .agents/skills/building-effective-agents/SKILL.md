---
name: building-effective-agents
description: Choose the right level of agentic complexity for a multi-step LLM system — the augmented LLM, the five workflow patterns (prompt chaining, routing, parallelization, orchestrator-workers, evaluator-optimizer), or a true autonomous agent — using Anthropic's own taxonomy. Use this whenever designing a new task graph or DAG node, deciding whether a step needs a full model call or just code, arguing about whether a design is "really" orchestrator-workers or just routing wearing a fancier name, reviewing a proposal to add a multi-agent or autonomous-loop shape, or checking whether added complexity is actually earning its keep before it ships.
---

# Building Effective Agents

Anthropic's own taxonomy for composing LLM calls into systems (Erik Schluntz & Barry Zhang, "Building Effective Agents," Dec 2024). One rule sits underneath every pattern below: **start with the simplest thing that could work, and add a pattern only once a simpler one has demonstrably fallen short.** For most tasks, one well-prompted call with good retrieval and a few examples is enough — reach for the rest only when it measurably isn't.

## The augmented LLM

Every pattern below composes one base unit: an LLM call enhanced with retrieval, tools, or memory. Nothing here is a new primitive — it's different ways of wiring that one unit together.

## Workflow vs. agent — decide this first

This is the load-bearing distinction, not a stylistic one:

- **Workflow**: code decides the next step. LLMs and tools run through a predefined path.
- **Agent**: the model decides the next step. It directs its own process and tool use in a loop, treating tool results as ground truth, until it decides the task is done.

Ask **"who picks what happens next — the code, or the model?"** before naming anything else. Almost everything below is a workflow shape; only the last section is a true agent.

## The five workflow patterns

| Pattern | Shape | Use when |
|---|---|---|
| **Prompt chaining** | Fixed sequence of LLM calls, each fed the previous one's output, often with a programmatic check between steps | The task decomposes cleanly into fixed subtasks — trading latency for higher accuracy on each step |
| **Routing** | One (often cheap) classification call picks a path; each path has its own specialized prompt | Inputs fall into a few distinct categories that are each better served by their own prompt than one generalized one |
| **Parallelization — sectioning** | Split a task into independent pieces, run them concurrently | The subtasks don't depend on each other and speed matters |
| **Parallelization — voting** | Run the *same* task multiple times, aggregate (majority vote, or require agreement) | You want higher confidence than a single pass gives you |
| **Orchestrator-workers** | A central call breaks the task down *at runtime* and dispatches to worker calls, then synthesizes their results | You can't predict the subtasks in advance — the decomposition itself depends on the specific input |
| **Evaluator-optimizer** | One call generates, a second evaluates and gives feedback, loop until satisfied | You have clear evaluation criteria and iterative refinement genuinely improves the answer |

**Routing vs. orchestrator-workers is the pair people conflate.** If the set of possible next steps is fixed in advance and a model just picks one, that's routing. Orchestrator-workers means the model invents the decomposition itself, per input — a fixed set of workers that always all run isn't this pattern, it's parallelization or chaining wearing an orchestrator's name. When a design calls itself "orchestrator-workers," ask whether a model call is genuinely choosing *which* sub-tasks run, or whether that was already decided in code before the model ever saw the input.

## When it's a true agent, not a workflow

Reach for an actual autonomous loop only for open-ended problems where the number of steps genuinely can't be predicted and no fixed path can be hardcoded. It buys flexibility at a real cost: higher spend and compounding-error risk, since one bad step can send everything sideways with nothing checking it until much later. That's why the source calls for extensive sandboxed testing and real guardrails before shipping one — not a caveat to skim past. If a workflow can be drawn as a fixed diagram, build it as a workflow; don't reach for a loop because it "feels more agentic."

## Before adding any pattern, ask

1. **What does a single well-prompted call get you, with good context, retrieval, and examples?** Measure that before adding structure on top of it.
2. **Is the next step decided by code or by the model?** Name it correctly — this decides which pattern you're actually building, and whether you're allowed to call it "agentic" at all.
3. **Is the decomposition fixed in advance, or does it depend on the specific input?** Fixed → chaining/parallelization/routing. Input-dependent → orchestrator-workers.
4. **Would a second model call checking the first one's output make the answer measurably better, or would a deterministic check catch the same problems for less?** Prefer the deterministic check when it covers the same ground — an evaluator-optimizer loop is a cost you pay for real iterative improvement, not a review step you add out of caution.
5. **If reaching for a true agent loop: can you actually bound and supervise it,** or are you buying flexibility you can't yet afford to test?

Runnable-code shapes for each pattern (from Anthropic's own cookbook) and the production cost/benefit gate for going multi-agent live in [`references/patterns.md`](references/patterns.md) — read it before implementing one of these patterns from scratch. Full citations: `docs/research/agentic-system-design/building-effective-agents.md`.

## Applying this to Friday

Friday's task graphs (`friday/dag/`) are workflows by this definition — the graph shape is chosen by `friday/dag/router.py`, never by a model — and every task type currently collapses to one node (extract → validate → ask-or-hand-over). That's a considered stopping point, not an unfinished one: nothing here argues a single-node workflow is under-built; the source argues the opposite, and CLAUDE.md's own history records the prior five-node `api_issue` graph being removed for being *undesigned*, not for being the wrong shape of thing.

When a board proposes something with "orchestrator" or "supervisor" in its name (as `.scratch/read-it-the-way-the-operator-does/`'s Diagnose/Collector design does), run the routing-vs-orchestrator-workers test above before accepting the label: if the Gather checks that run are fixed in advance and Diagnose only interprets their already-computed output, that's routing/chaining with one LLM step, not orchestrator-workers — and Friday's own architecture rule that "a model never chooses the next graph step" means a true orchestrator-workers shape needs an *explicit*, named exception, not an implicit one that slides in under a pattern name nobody checked.
