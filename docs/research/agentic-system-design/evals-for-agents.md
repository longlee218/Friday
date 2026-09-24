# Demystifying Evals for AI Agents — Anthropic

Anthropic Engineering Blog post, published ~January 2026: field-tested guidance for
evaluating agentic systems, drawn from Anthropic's work with customers building coding
agents, research agents, and conversational AI. Exact title found — no substitution
needed. Primary URL, fetched directly:
https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents

## Key concepts

**Vocabulary.** The post fixes eight terms so a team can talk about the same thing:
a **task** ("a single test with defined inputs and success criteria"), a **trial**
("each attempt at a task"; run multiple because outputs vary), the **agent
harness/scaffold** (orchestrates tool calls, returns results), the **eval harness**
(runs tasks concurrently, records steps, grades, aggregates), the **transcript/trace**
("the complete record of a trial, including outputs, tool calls, reasoning,
intermediate results"), the **outcome** ("the final state in the environment," e.g.
whether a reservation actually exists in the SQL database, not just confirmation
text), the **grader** (scoring logic; a task can have several, each with multiple
assertions), and the **suite** (a collection of tasks measuring a capability).

**Why agents are harder to grade than chat.** Single-turn eval is "a prompt, a
response, and grading logic." Agents "use tools across many turns, modifying state
in the environment and adapting as they go — which means mistakes can propagate and
compound." The fix is to grade the environment's end **outcome**, not the agent's
claimed summary, and to keep the full transcript so a failure can be read, not just
scored.

**Building task-based evals.** Start small and real: "20-50 simple tasks drawn from
real failures is a great start," converted from user-reported failures and existing
manual checks, prioritized by impact. A task is only good if "two domain experts
would independently reach the same pass/fail verdict," with a human-authored
reference solution proving solvability. Tellingly: "with frontier models, a 0% pass
rate across many trials is most often a signal of a broken task, not an incapable
agent." Sets should be balanced — cases where a behavior should *and* shouldn't
occur — to avoid class imbalance.

**Grader types and tradeoffs.** Code-based graders are fast, cheap, reproducible,
and easy to debug, but brittle to valid variations the designer didn't anticipate.
Model-based (LLM-as-judge) graders are flexible and handle open-ended/freeform
output, but are non-deterministic, costlier, and need calibration. Human graders
are the gold standard and calibrate the model-based ones, but are expensive and
slow. Recommendation: "choose deterministic graders where possible, LLM graders
where necessary or for additional flexibility, and use human graders judiciously
for additional validation."

**LLM-as-judge pitfalls named explicitly:** grade one dimension per judge call
rather than one judge scoring everything at once; calibrate closely against human
experts; and to suppress hallucination, "give the LLM a way out, like … an
instruction to return 'Unknown' when it doesn't have enough information." Once
calibrated, human review only needs to happen occasionally, not on every run.

**Tool-selection/routing evals.** Called out specifically for computer-use agents:
Anthropic's Claude for Chrome team "developed evals to check that the agent was
selecting the right tool for each context" — e.g., DOM extraction vs. screenshots
depending on which is cheaper for the task at hand. This is presented as its own
eval category, distinct from outcome correctness.

**Avoiding brittle step-checking / reading failures fairly.** The post explicitly
warns against grading the exact sequence of steps an agent took: "we've found this
approach too rigid and results in overly brittle tests, as agents regularly find
valid approaches that eval designers didn't anticipate." Reading the transcript is
how you tell "whether the agent made a genuine mistake or whether your graders
rejected a valid solution" — failures should "seem fair."

**Iterating eval sets over time.** Capability evals ("what can this agent do well?")
should start at a low pass rate, targeting hard cases; once an agent is launched and
optimized, those same tasks can "graduate" into a regression suite expected to stay
near 100%. Watch for **saturation** — passing every solvable task, leaving no
signal — and author harder tasks before that happens. An eval suite is "a living
artifact that needs ongoing attention and clear ownership," ideally a dedicated
evals team owning infrastructure while domain experts and product teams supply most
of the tasks; "eval-driven development" (writing evals for a capability before the
agent can do it) is named as a practice worth adopting.

**Ground truth authority (implicit).** The post doesn't use the phrase "model-graded
labels," but its throughline is that reference solutions and pass/fail judgment
come from people "closest to product requirements and users" — domain experts,
PMs, customer-success staff — not from the system under test.

## Related primary sources found

- Primary post (fetched and cited above):
  https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
- Anthropic webinar, same topic, found via search but not fetched — listed for
  completeness, not cited above: "Evals for AI Agents: How Product Builders Get the
  Most Out of Every New Model,"
  https://www.anthropic.com/webinars/evals-for-ai-agents-how-product-builders-get-the-most-out-of-every-new-model
- Anthropic's own announcement of the post (context only, not a separate source):
  https://x.com/AnthropicAI/status/2009696515061911674

No document titled exactly "Demystifying Evals for AI Agents" existed anywhere but
the blog itself — the title match was exact, so no substitution was needed.

## Relevance to Friday

**The eval's biggest weakness — model-chosen labels.** Friday's own project notes
already name this gap: `evals/triage.jsonl` is scored against labels the classifier
model chose, not an operator. Anthropic's framing sharpens why that's a real defect
rather than a formality: a **task**'s pass/fail verdict has to be one "two domain
experts would independently reach," and their implicit chain of authority routes
ground truth through people closest to the product and its users — here, the
Discord operator, not the model under test. A classifier graded against its own
priors can only ever measure self-consistency, never accuracy. This validates
Friday's `ready-for-human` ticket rather than adding a new argument to it.

**Building the missing evals.** Friday has three eval-shaped gaps today: extraction
accuracy, tool-selection for the planned Collector/investigation agent, and the
Diagnose flow generally. Anthropic's playbook maps directly: start from 20-50 real
failed extractions (Friday already has the material — every hand-over and every
`Refused` is a natural seed), require a human-written reference `Params` object per
task so pass/fail is checkable, and grade the **outcome** (did the task end up with
correct parameters) rather than the extractor's transcript shape. For the future
Collector agent's tool-selection, the post's Claude-for-Chrome example is a close
analogue to Friday's own `fetch_skill`/`search_skills`/`describe_skill`/
`read_skill_file` split — an eval there should check whether the agent reached for
the *right* tool given what it already knew, exactly the kind of thing
`friday/kernel/tools/` currently logs (tool calls travel the same recording sink as model
calls) but never scores.

**Out-of-set vs. genuine outage — recognized pattern or Friday invention?** The post
does not name this split. Its closest relative is the general instruction to read
transcripts to separate "a genuine mistake" from "your graders rejected a valid
solution" — a grader-quality distinction, not a model-vs-infrastructure one.
Friday's split — `NeedsHuman.out_of_set` (the model answered outside a closed enum,
a prompt/model problem) counted separately from a provider outage (a network
problem) — is a finer-grained, system-specific pattern the source doesn't describe.
It's consistent with the source's spirit (measure precisely enough to act on the
number) but goes further than anything documented here: this is Friday's own
contribution, not an application of Anthropic's own taxonomy.
