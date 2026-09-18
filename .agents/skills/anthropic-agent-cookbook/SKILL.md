---
name: anthropic-agent-cookbook
description: Reference the Anthropic Cookbook's actual runnable implementations of agent patterns — prompt chaining, routing, parallelization, orchestrator-workers, evaluator-optimizer, async multi-agent orchestration, and Claude Agent SDK dynamic workflows — to see the real code shape and its known limitations before implementing or reviewing a similar pattern from scratch. Use this when implementing one of these patterns, comparing a proposed design (a DAG node, a sub-agent split, an orchestrator) against a known first-party reference implementation, or deciding where a new workflow's control flow and plan should actually live (single agent, fixed subagent team, or a script).
---

# Anthropic Agent Cookbook

The runnable code counterpart to the taxonomy in `building-effective-agents`: `anthropics/claude-cookbooks` (the older `anthropic-cookbook` URL now redirects here), pattern notebooks under `patterns/agents/`, newer SDK-native examples under `claude_agent_sdk/`.

## What's actually in each notebook

**Prompt chaining, routing, parallelization** (`basic_workflows.ipynb`) are one-function implementations over two primitives: a thin LLM-call wrapper and an XML-tag extractor (no JSON mode, no schema validation). `route()` makes a single classification call over a **hardcoded** set of route keys — the decomposition is fixed in code, not discovered.

**Orchestrator-workers** (`orchestrator_workers.ipynb`): one call returns the task breakdown at runtime (the actual defining feature of this pattern — the decomposition isn't fixed in advance), then the code loops over the resulting tasks calling a worker prompt for each. Two limitations worth knowing before copying this shape: the reference loop runs **sequentially**, with parallelizing it left as an explicit exercise, and it ships with **no synthesis step** at all — combining the workers' outputs into a final answer isn't implemented; the notebook's own "next steps" names this as unfinished.

**Evaluator-optimizer** (`evaluator_optimizer.ipynb`): generate → evaluate (checks literally for the string `"PASS"`) → if not passing, rebuild context with every prior attempt's feedback → generate again. **As published, this loop has no turn cap.** Any adoption with a bounded correction budget needs to add that cap explicitly — it isn't part of the reference shape.

**Async multi-agent orchestration** (`async_multi_agent_orchestration.ipynb`): a `Hub` with per-agent inboxes; `send_message`/`wait_for_message` are the *only* channel between agents, with each turn's inbox appended onto the last tool result rather than polled. Two variants: a fixed N-agent team started up front, versus a dynamic lead with extra tools (`create_subagents`/`get_status`/`kill_subagents`) that spawns and tears down helpers at runtime — the dynamic version is a true agent by the workflow/agent test, since nobody decided the team size in code.

**Dynamic workflows** (`claude_agent_sdk/08_Dynamic_workflows.ipynb`) — a different, newer approach: Claude writes a small orchestration script itself (`agent()`, `parallel()`, `pipeline()`, `phase()` primitives), a runtime executes it, and **plain code runs between calls** for filtering/branching/merging at zero token cost. Each spawned agent gets a clean context — it sees only its own prompt, nothing of the lead's history. The notebook's own "who holds the plan" framing is the sharpest tool here for deciding control-flow shape:

| Shape | Plan lives in | Scales to |
|---|---|---|
| Single agent | The model's own context, turn by turn | One thread of work |
| Fixed subagent team | The lead agent's context, turn by turn | A handful of parallel branches |
| Dynamic workflow (script) | Script variables — resumable, inspectable | Dozens to hundreds of agents |

As fan-out grows, moving the plan out of a model's context and into code is what keeps a design reviewable and resumable — a useful gut-check independent of whether you're using this specific SDK feature.

## The multi-agent research system's real prompts

Shipped in the same repo (`patterns/agents/prompts/`): a **lead agent** prompt that classifies a query's shape (depth-first / breadth-first / straightforward) and sizes a subagent count (1–20) by complexity before delegating; a **subagent** prompt where each instance sets its own tool-call budget up front and runs an explicit observe-orient-decide-act loop within it; and a **citations agent** that runs as a strictly separate final pass, only ever adding citations, never touching report text. Worth reading directly if you're building anything with a lead/subagent split — this is the actual production prompt behind Anthropic's multi-agent research post, not a simplified illustration of it.

Condensed per-notebook code shapes and the fact-check-workflow worked example live in [`references/pattern-code-shapes.md`](references/pattern-code-shapes.md). Full citations: `docs/research/agentic-system-design/anthropic-agent-cookbook.md`.

## Applying this to Friday

**None of the `patterns/agents` examples use a hand-rolled, checkpointed graph framework like `friday/dag/engine.py`** — they lean on bare loops or the SDK's own orchestration. The *dynamic workflows* notebook is the closest cookbook analogue to what Friday built by hand: a script (there, JS generated by Claude at runtime; in Friday, Python written ahead of time) deciding node order deterministically, with model calls as opaque steps and control flow in plain code between them. The cookbook's own "who holds the plan" table reaches the same conclusion `docs/DESIGN.md` argues for — plan-in-code beats plan-in-context past a certain fan-out — from the runtime-feature side rather than the hand-rolled-framework side.

**The dynamic-workflows fact-check example (EXTRACT → VERIFY-parallel → SKEPTIC → REPORT) maps cleanly onto the Gather → Diagnose → Collector shape** being designed on `.scratch/read-it-the-way-the-operator-does/`: Friday's parallel Source checks ≈ the workflow's parallel VERIFY fan-out (each check reads one thing fresh, no shared context between them); Friday's Diagnose reasoning step ≈ the workflow's code-level routing logic deciding what runs next; Friday's bounded Collector sub-agent call ≈ the workflow's single-purpose `agent()` spawn with a clean, narrow context. The chattier `async_multi_agent_orchestration` inbox pattern is a worse fit for this — it's built for long-lived bidirectional peers, not a bounded one-shot fetch.

**The evaluator-optimizer loop's missing turn cap is a direct warning, not a footnote**, if this pattern is ever adopted for a future Diagnose-quality gate: Friday's own stated philosophy is a one-turn correction budget everywhere else in the system, and copying this loop unmodified would ship the one pattern in the entire cookbook that has no bound on how long it runs.
