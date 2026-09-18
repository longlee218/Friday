---
name: evals-for-agents
description: Build and review evals for agentic systems — task design, grader types (code, model, human), LLM-as-judge calibration, tool-selection evals, and growing a suite from a low-pass-rate capability test into a near-100% regression net. Use this whenever creating or reviewing an eval set (including Friday's evals/triage.jsonl and run_triage_eval.py), deciding how to grade a multi-step agent trajectory, choosing between a code-based grader and an LLM-as-judge, noticing an eval is scored against the wrong ground truth, or wondering whether an eval set has saturated and stopped producing signal.
---

# Demystifying Evals for AI Agents

Anthropic's field-tested guidance for evaluating agentic systems (`anthropic.com/engineering/demystifying-evals-for-ai-agents`), drawn from work with customers building coding agents, research agents, and conversational AI.

## Shared vocabulary

Fix these terms so a team argues about the actual disagreement, not what a word means:

- **Task** — a single test with defined inputs and success criteria.
- **Trial** — one attempt at a task; run several, since outputs vary.
- **Agent harness/scaffold** — orchestrates the tool calls, returns results.
- **Eval harness** — runs tasks concurrently, records steps, grades, aggregates.
- **Transcript/trace** — the complete record of a trial: outputs, tool calls, reasoning, intermediate results.
- **Outcome** — the final state *in the environment* (does the reservation actually exist in the database?) — not the agent's own summary of what it did.
- **Grader** — scoring logic; a task can have several graders, each with multiple assertions.
- **Suite** — a collection of tasks measuring one capability.

## Why agents are harder to grade than chat

A single-turn eval is a prompt, a response, and grading logic. An agent uses tools across many turns, modifying state as it goes and adapting along the way — so mistakes can propagate and compound in ways a single bad answer can't. The fix: grade the environment's end **outcome**, not the agent's own claim of success, and always keep the full transcript so a failure can be *read*, not just scored.

## Building task-based evals

Start small and real: 20–50 simple tasks drawn from actual failures is a strong first set, converted from user reports or existing manual checks and prioritized by impact. A task is only good if two domain experts would independently reach the same pass/fail verdict on it — write a human-authored reference solution to prove the task is actually solvable before trusting a 0% result as "the agent can't do this." A 0% pass rate across many trials, with a frontier model, is far more often a broken task than an incapable agent. Balance the set: include cases where a behavior *should* and *shouldn't* occur, to avoid every task quietly testing the same direction.

## Grader types

| Grader | Strengths | Weaknesses |
|---|---|---|
| **Code-based** | Fast, cheap, reproducible, easy to debug | Brittle to valid variations the designer didn't anticipate |
| **Model-based (LLM-as-judge)** | Flexible, handles open-ended/freeform output | Non-deterministic, costlier, needs calibration |
| **Human** | Gold standard, calibrates the other two | Expensive, slow — not for every run |

Recommendation, in order of preference: deterministic graders where possible, LLM graders where necessary or for extra flexibility, human graders judiciously for validation — not as the primary grading mechanism at scale.

**LLM-as-judge pitfalls**: grade one dimension per judge call rather than one call scoring everything at once; calibrate closely against human experts before trusting it unsupervised; and give the judge an explicit way out — an instruction to answer "Unknown" when it doesn't have enough information — to suppress confident hallucination. Once calibrated, human review only needs to run occasionally, not on every eval pass.

## Tool-selection evals are their own category

Distinct from outcome correctness: does the agent pick the *right tool* for the situation, not just get the right final answer? Anthropic's own example — a browser agent choosing DOM extraction vs. screenshots depending on which is cheaper for the task at hand — is worth building an eval for whenever an agent has more than one way to accomplish the same thing and the choice matters for cost or reliability.

## Don't grade the exact steps

Grading an agent's precise sequence of actions produces overly brittle tests — agents regularly find valid approaches an eval designer didn't anticipate, and a rigid step-checker will fail them for it. Read the transcript to tell whether a failure is a genuine mistake or the grader rejecting a valid solution. A failing eval should *seem fair* to someone reading the transcript cold.

## Iterating the suite over time

A capability eval ("what can this agent do well?") should start hard, at a low pass rate. Once an agent is launched and optimized against it, those same tasks graduate into a regression suite expected to stay near 100%. Watch for **saturation** — passing everything, producing no signal — and write harder tasks before that happens. Treat the suite as a living artifact with clear ownership, not a one-time deliverable; "eval-driven development" (writing the eval for a capability before the agent can do it) is named explicitly as a practice worth adopting rather than writing evals only after something breaks.

Step-by-step playbook and the ground-truth-authority argument in full live in [`references/eval-playbook.md`](references/eval-playbook.md). Full citations: `docs/research/agentic-system-design/evals-for-agents.md`.

## Applying this to Friday

**The one existing eval's real weakness**: `evals/triage.jsonl` is scored against labels the classifier model itself chose, not an operator. By this source's own criterion — a task's verdict has to be one two *domain experts* would agree on, with ground truth routed through the people closest to the product — a classifier graded against its own prior labels can only measure self-consistency, never accuracy. This is already tracked as a `ready-for-human` gap; treat it as validated, not as a new finding to relitigate.

**Building the missing evals** (extraction accuracy, tool-selection for a future Collector/investigation agent): seed them from real hand-overs and `Refused` extractions already sitting in the system, write a human-authored reference `Params` object per case, and grade the outcome (did the task end up with the right parameters) rather than the shape of the extractor's transcript.

**A pattern Friday invented, not one it borrowed**: counting `out_of_set` (the model answered outside the closed enum — a prompt/model problem) separately from a provider outage (an infrastructure problem) doesn't appear in this source. It's consistent with the source's spirit — measure precisely enough to act on the number — but it's Friday's own contribution, not an application of a documented Anthropic pattern; don't cite this source as the origin of that specific distinction.
