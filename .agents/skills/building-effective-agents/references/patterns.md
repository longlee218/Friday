# Pattern code shapes and the multi-agent cost gate

Drawn from `anthropics/claude-cookbooks`, `patterns/agents/` — the runnable counterpart to the taxonomy in `SKILL.md`. Read this when you're about to implement one of these patterns and want the actual control-flow shape, not just the name.

## Prompt chaining, routing, parallelization

All three are one-function implementations built on two primitives: `llm_call(prompt, system_prompt)` (a thin wrapper over the API) and `extract_xml(text, tag)` (regex tag extraction — no JSON mode, no schema validation).

- **`chain(input, prompts: list[str])`** folds a list of prompts sequentially: each step's output becomes the next step's input string. Insert a programmatic check between steps whenever a step's output has a validatable shape — that's where chaining earns the "higher accuracy per step" trade the pattern promises.
- **`parallel(prompt, inputs, n_workers=3)`** fans the *same* prompt out over a thread pool and collects results — no synthesis step, just a list back. This is sectioning or voting depending on whether `inputs` differ or repeat.
- **`route(input, routes: dict[str, str])`** makes one classification call asking the model to pick a key from `routes.keys()`, wrapped in `<reasoning>`/`<selection>` tags, then dispatches to that route's prompt. The key set is hardcoded, not discovered at runtime — that's what keeps this "routing" and not "orchestrator-workers."

## Orchestrator-workers

A `FlexibleOrchestrator` runs two phases: (1) one call to an orchestrator prompt returns an analysis plus a list of `<task><type>/<description></task>` entries — the decomposition, chosen by the model, at runtime, from the specific input; (2) the code loops over those tasks, calling a worker prompt once per task. Two things worth noticing before copying this shape: the reference notebook loops **sequentially**, not in parallel, and flags that as a known limitation rather than a design choice — don't inherit that limitation by accident. And it ships with **no synthesis step** — the workers' outputs come back as a list, and combining them into a final answer is left as an exercise. If your version of this pattern needs synthesis (most real uses do), that's a third phase you have to add, not something implicit in "orchestrator-workers" as a name.

## Evaluator-optimizer

`generate()` produces a `(thoughts, result)` pair; `evaluate()` returns `(verdict, feedback)` where `verdict` is expected to literally be the string `"PASS"` or something else; `loop()` calls `generate` once, then alternates `evaluate` → (if not `"PASS"`) rebuild context with every previous attempt's feedback → `generate` again. **As published, this loop has no turn cap** — it runs until the evaluator says `PASS`, however long that takes. If you're adopting this pattern anywhere with a bounded correction budget (a fixed number of retries, a cost ceiling), that cap is something you add — it is not part of the reference shape, and skipping it means shipping a loop that can spin.

## Async multi-agent orchestration

A `Hub` class holds per-agent inboxes; two tools, `send_message` / `wait_for_message`, are the *only* channel between agents, and each agent's turn appends its drained inbox onto the last tool result rather than polling. Two variants worth distinguishing: a **fixed N-agent team** (peers named and started up front) versus a **dynamic lead** that gets extra tools (`create_subagents`, `get_status`, `kill_subagents`) to spawn and tear down helpers at runtime. The dynamic variant is a true agent by the workflow/agent test above — nobody decided the team size in code; the lead model did.

## Claude Agent SDK: dynamic workflows

A newer, distinct approach: Claude itself writes a small orchestration script (`agent()`, `parallel()`, `pipeline()`, `phase()` primitives) that a runtime executes, with **plain code between calls** for filtering, branching, and merging at zero token cost, and each spawned agent getting a **clean context** — it sees only the prompt it was given, nothing of the lead's history. The notebook's own decision table is the sharpest summary of when to reach for this over a single agent or a fixed subagent team:

| Shape | Who holds the plan | Scales to |
|---|---|---|
| Single agent | Claude, turn by turn, plan in its own context | One thread of work |
| Subagents (fixed team) | Claude, turn by turn, plan lives in the lead agent's context | A handful of parallel branches |
| Dynamic workflow (script) | A script decides; plan lives in script variables, resumable | Dozens to hundreds of agents |

This table is a useful gut-check independent of whether you're using this SDK feature: as fan-out grows, moving the *plan* out of a model's context and into code is what keeps a design reviewable and resumable — the same argument for building deterministic workflow code in general.

## The multi-agent cost gate

Before reaching for any multi-agent shape, Anthropic's own production case study ("How we built our multi-agent research system") gives the sharper test: multi-agent is worth its cost only for tasks with **heavy parallelization, information exceeding a single context window, and interfacing with numerous complex tools** — not for tasks with few parallelizable components (its own counterexample: coding tasks) or tasks needing shared context across every agent. Run this check *in addition to*, not instead of, the workflow-vs-agent question — a task can clearly need a model-directed loop and still be too small or too sequential to justify paying for more than one agent.
