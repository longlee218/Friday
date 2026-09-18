# How real agents structure multi-step investigation work

Written 2026-09-17 for board `read-it-the-way-the-operator-does`, against
the spec's `api_issue` graph (Prepare → Route → Notify → investigators →
Diagnose → Explain → Report). Every URL below was fetched on 2026-09-17,
either by me or by one of four background research agents whose notes I
checked against my own fetches where they overlapped. Where a claim rests
on a vendor's own marketing rather than code or a paper, it says "vendor
claim". Where no primary source was reached, it says "not verified".

## Comparison table

| System | Control flow | Unit of step | Checkpoint / resume | Human-in-the-loop | Evidence / result shape |
| --- | --- | --- | --- | --- | --- |
| OpenHands (software-agent-sdk) | Model-defined loop; `StuckDetector` and `max_iterations` (500) bound it | One `agent.step()` = one LLM call → N `ActionEvent` → N `ObservationEvent`, keyed by `tool_call_id` | `EventLog`, one JSON file per event; resume by reopening the same `conversation_id` | `ConfirmationPolicy` (`AlwaysConfirm`, `ConfirmRisky(threshold)`), reject → `UserRejectObservation` | `Observation(content, is_error)` |
| SWE-agent | Model-defined loop (`Agent.forward()`); `RetryAgent` wrapper is the only code structure | `TrajectoryStep{action, observation, response, thought, state, query}` | `.traj` saved after every step; a record, not a resume point | None; `HumanModel` replaces the LM with `input()` | Free text, search capped at 50 results |
| Aider architect/editor | Code-defined two-step pipeline; reflection loop capped at 3 | A model call | Chat history file; git commits are the durable state | `confirm_ask` per edit | Free text |
| Claude Code subagents | Model chooses to delegate; hooks and permission rules are code | Tool-use turn | Session JSONL; `SendMessage` resumes a subagent | Permission modes, `PreToolUse` hooks, `canUseTool` callback | Condensed summary returned to the parent |
| Google ADK | Both: `Sequential`/`Parallel`/`LoopAgent` and 2.0 graph `Workflow` are code; `LlmAgent` routes dynamically | `Event` yielded to the `Runner` | `ResumabilityConfig`; resume skips finished sub-agents; tools may run twice | `LongRunningFunctionTool` (pending → `FunctionResponse`), `require_confirmation` | `session.state` dict, `output_key` |
| OpenAI Agents SDK | Both; docs recommend "orchestrating via code" for determinism | Runner turn, `max_turns` | `RunState.to_json()`; only approvals pause a run, no per-step checkpoint | `needs_approval` → `RunResult.interruptions` → `approve`/`reject` → rerun | Structured outputs chained by code |
| AutoGen GraphFlow | Code-defined DAG (`DiGraphBuilder`), conditional edges, fan-out/join, loops; `SelectorGroupChat` is the model-routed alternative | Agent response | `save_state()`/`load_state()` (model context); no per-node checkpoint | `UserProxyAgent` blocks the team; handoff to `"user"` | Chat messages |
| Semantic Kernel Process | Code-defined event-driven steps; experimental | One event into one kernel function | Stateful steps checkpoint; Dapr runtime for durability | .NET only: process idles on a missing event, restarts on `StartAsync(event)` | Per-step state object |
| CrewAI Flows | Code-defined (`@start`, `@listen`, `@router`, `or_`/`and_`); Crews are the model-driven part | Flow method | `@persist` to SQLite; `kickoff(inputs={"id"})` resumes, `restore_from_state_id` forks | `@human_feedback` pauses; free text collapsed to a route label | Pydantic or dict state |
| LangGraph | Code-defined `StateGraph`; a conditional edge may call a model | Super-step | Checkpointer per super-step; durability `exit`/`async`/`sync`; resume re-runs the node from its start | `interrupt()` + `Command(resume=…)`; node re-runs, so pre-interrupt code must be idempotent | Typed state with reducers |
| HolmesGPT | Model-defined tool loop, `max_steps` (default 100); tools withheld on the last step | LLM call + parallel tool calls (16 workers) | None within a run; history compaction only | None within a run | `StructuredToolResult{status, data, error}`; per-tool cap min(15% of context, 25k tokens), spilled to disk |
| k8sgpt | Code-defined: fixed analyzer set every run, then one LLM call per failure | One `Analyzer` | Stateless; explanations cached by failure text | None | `Result{Kind, Name, Error []Failure{Text, Sensitive}, Details, ParentObject}` |
| Databricks AI SRE | Code-defined checks first, model synthesises (vendor post) | Check | Not public | Engineer reads the linked evidence | Every conclusion links to "the specific metric, the log line, the deploy diff" |
| Datadog Bits AI SRE | Hybrid: hypothesis tree, model-driven loop per branch (vendor) | Hypothesis | Not public | Slack | Hypothesis `validated | invalidated | inconclusive` |
| incident.io | Hybrid: parallel checks, then findings → hypotheses → critique (docs) | Check, then finding | Not public | Slack report | Finding = claim; evidence = "a specific Slack message, a pull request diff, a metric spike, a line of code"; five-rung confidence ladder |
| PagerDuty SRE Agent | Hybrid: supervisor spawns one sub-agent per candidate cause (vendor) | Hypothesis sub-agent | Durable supervisor, stateless sub-agents | Cancel | Evidence "supporting or disproving its hypothesis" |
| Gemini Cloud Assist | Hybrid: parallel analysis over fixed sources, model ranks "Observations" | Observation | Investigation object, re-runnable | Chat, support-case hand-off | Ranked Observations → hypotheses with "recommended fixes to confirm or refute" |
| RCACopilot (Microsoft) | Code-defined: per-alert-type handler collects fixed diagnostics, then one LLM step | Handler | n/a | Engineer reads narrative | Aggregated diagnostics + predicted category |

## 1. How the general-purpose agents structure a multi-step task

**The coding agents are ReAct loops with a hard budget, not graphs, and
they share one shape:** the step is an action/observation pair keyed on a
tool-call id, persisted as an append-only log, stopped by a finish tool, an
iteration cap, a cost cap, or loop detection. OpenHands' current SDK loops
`agent.step()` inside `LocalConversation.run()` until `FINISHED`, `PAUSED`,
`STUCK` or `WAITING_FOR_CONFIRMATION`; `StuckDetector` scans the last 20
events for repeats; every tool returns `Observation(content, is_error)`
([agent.py](https://raw.githubusercontent.com/OpenHands/software-agent-sdk/main/openhands-sdk/openhands/sdk/agent/agent.py),
[local_conversation.py](https://raw.githubusercontent.com/OpenHands/software-agent-sdk/main/openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py),
[stuck_detector.py](https://raw.githubusercontent.com/OpenHands/software-agent-sdk/main/openhands-sdk/openhands/sdk/conversation/stuck_detector.py),
[schema.py](https://raw.githubusercontent.com/OpenHands/software-agent-sdk/main/openhands-sdk/openhands/sdk/tool/schema.py)).
Persistence is one JSON file per event under `events/`, and resume is
reopening the same conversation id ([event_store.py](https://raw.githubusercontent.com/OpenHands/software-agent-sdk/main/openhands-sdk/openhands/sdk/conversation/event_store.py),
[persistence guide](https://docs.openhands.dev/sdk/guides/convo-persistence.md)).
Human-in-the-loop is a `ConfirmationPolicy` over a model-predicted
`SecurityRisk` ([confirmation_policy.py](https://raw.githubusercontent.com/OpenHands/software-agent-sdk/main/openhands-sdk/openhands/sdk/security/confirmation_policy.py));
the legacy config has `max_iterations` 100 and `[security] confirmation_mode`
([configuration options](https://docs.openhands.dev/openhands/usage/v0/advanced/V0_configuration-options)).
SWE-agent's `DefaultAgent.run()` is `while not step_output.done: step();
save_trajectory()`, a `.traj` per step holding `action, observation,
response, thought, state, query`, ended by `exit_cost`, `exit_context` and
friends ([agents.py](https://raw.githubusercontent.com/SWE-agent/SWE-agent/main/sweagent/agent/agents.py),
[trajectories](https://swe-agent.com/latest/usage/trajectories/)); the docs
say the project is superseded by mini-swe-agent
([architecture](https://swe-agent.com/latest/background/architecture/)).

**Aider is the counter-example: a two-node code-defined pipeline.** The
architect model proposes, the editor model applies (`ArchitectCoder`,
`reply_completed()` spawns the editor), and the split lifted pass rates from
55.6–79.7% for single models to 85.0% for the best pair
([architect post](https://aider.chat/2024/09/26/architect.html),
[architect_coder.py](https://raw.githubusercontent.com/Aider-AI/aider/main/aider/coders/architect_coder.py)).
Separating "reason" from "produce the formatted output" is the move the spec
makes with Diagnose and Report.

**Claude Code puts the graph in the model's hands, with isolation and hooks
as the code.** A subagent is a markdown file with `name`, `description`,
`tools`, `model`, `maxTurns`; Claude "automatically decides to delegate";
only the summary returns; subagents run in parallel or chained
([sub-agents](https://code.claude.com/docs/en/sub-agents)). `PreToolUse`
hooks return `permissionDecision: allow|deny`, and a `Stop` hook with exit 2
"prevents Claude from stopping" ([hooks](https://code.claude.com/docs/en/hooks)).

**The workflow SDKs all ship both modes and all recommend code when the
steps are known.** ADK: template workflow agents "operate based on
predefined logic … deterministic and predictable execution patterns", and
ADK 2.0 adds a graph `Workflow` where "each node's return value is passed to
the next node as its input" ([workflow agents](https://adk.dev/agents/workflow-agents/),
[graphs](https://adk.dev/graphs/)); its own advice is to "interweave the
non-deterministic functionality of AI models with deterministic code"
([agents](https://adk.dev/agents/)). Resume is opt-in
`ResumabilityConfig`, finished sub-agents are skipped, and "tools may
execute more than once", so idempotency is the author's
([resume](https://adk.dev/runtime/resume/)). Results flow through
`session.state` via `output_key` ([state](https://adk.dev/sessions/state/)).
OpenAI: "orchestrating via code" is "more deterministic and predictable, in
terms of speed, cost and performance" ([multi-agent](https://openai.github.io/openai-agents-python/multi_agent/));
HITL is `needs_approval` → `RunResult.interruptions` → `RunState.to_json()`
→ `approve`/`reject` → `Runner.run(agent, state)`, and that approval is the
only pause point ([human in the loop](https://openai.github.io/openai-agents-python/human_in_the_loop/),
[run state](https://openai.github.io/openai-agents-python/ref/run_state/)).
AutoGen's `GraphFlow` is a `DiGraphBuilder` DAG with string or callable edge
conditions, fan-out, join and `activation_condition`, for "strict control
over the order in which agents act", against `SelectorGroupChat` where a
model picks the speaker ([GraphFlow](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/graph-flow.html));
`save_state()` holds the model context ([state](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/state.html)).
Semantic Kernel's Process Framework is `Process`/`Step`/`Pattern`, marked
experimental; stateful steps checkpoint and Dapr is the durable runtime;
Python HITL is "coming soon"
([process framework](https://learn.microsoft.com/en-us/semantic-kernel/frameworks/process/process-framework),
[human in loop](https://learn.microsoft.com/en-us/semantic-kernel/frameworks/process/examples/example-human-in-loop)).
CrewAI Flows: `@start`, `@listen`, `@router`, `or_`/`and_`, `@persist` to
SQLite, `@human_feedback` collapsing free text into a route label; the docs
say "start with a Flow" and put crews inside steps
([flows](https://docs.crewai.com/en/concepts/flows),
[human feedback](https://docs.crewai.com/en/learn/human-feedback-in-flows),
[introduction](https://docs.crewai.com/en/introduction)).

**LangGraph is the closest to friday's engine, and its two rules are the
ones friday already learned.** Durability has three modes, `exit`, `async`,
`sync`; on resume "execution resumes at the beginning of the node where it
halted"; "wrap any non-deterministic operations … and any operations with
side effects … inside tasks or nodes"
([Durability reference](https://reference.langchain.com/python/langgraph/types/Durability),
[durable execution, unofficial mirror](https://github.com/kyoron2/LangChain-projectsdocs/blob/main/src/oss/langgraph/durable-execution.mdx);
the official page redirected to the persistence overview when fetched).
`interrupt()` pauses, "the node restarts from the beginning of the node
where the interrupt was called when resumed", `Command(resume=value)`
becomes the interrupt's return value
([interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)).
That is the spec's "notify is idempotent on `outbound_count`" and "resume
is a re-run from route", stated by a framework with a much larger user base.
Every framework with real resume (ADK, LangGraph, SK, CrewAI) re-runs the
interrupted unit from its start; none resumes mid-node.

## 2. SRE and incident-investigation agents

**HolmesGPT is a bounded ReAct loop with a strict prompt, not a graph.**
`ToolCallingLLM.call_stream()` runs `while i < max_steps` (default 100 in
`holmes/config.py`), passes `tools=` to `completion()` so the model chooses,
sets `tools = None if i == max_steps else tools` to force a final answer,
runs a turn's tool calls in a `ThreadPoolExecutor(max_workers=16)`, and
raises "Too many LLM calls - exceeded max_steps" on overrun
([tool_calling_llm.py](https://raw.githubusercontent.com/robusta-dev/holmesgpt/master/holmes/core/tool_calling_llm.py),
[config.py](https://raw.githubusercontent.com/robusta-dev/holmesgpt/master/holmes/config.py)).
Every tool returns `StructuredToolResult(status, data, error, params)`
([tools.py](https://raw.githubusercontent.com/robusta-dev/holmesgpt/master/holmes/core/tools.py)).
A result over `min(context_window × 15%, 25,000 tokens)` is spilled to disk
and replaced by a pointer so the model re-queries narrower; the whole
history is compacted by a summarising call when it nears the window
([tool_context_window_limiter.py](https://raw.githubusercontent.com/robusta-dev/holmesgpt/master/holmes/core/tools_utils/tool_context_window_limiter.py),
[env_vars.py](https://raw.githubusercontent.com/robusta-dev/holmesgpt/master/holmes/common/env_vars.py)).
Planning is itself a tool, `TodoWrite`, mandated for "any investigation
requiring 3+ distinct steps"
([core_investigation.py](https://raw.githubusercontent.com/robusta-dev/holmesgpt/master/holmes/plugins/toolsets/investigator/core_investigation.py)).
The prompt carries the diagnosis discipline: "Distinguish confirmed facts
(directly observed in tool output) from hypotheses", quote logs verbatim,
"state plainly what wasn't checked rather than guessing", and separate "I
investigated and found error X" from "errors prevented me from completing
the investigation" ([generic_ask.jinja2](https://raw.githubusercontent.com/robusta-dev/holmesgpt/master/holmes/plugins/prompts/generic_ask.jinja2)).
Disabled toolsets are listed in the prompt as disabled, which is how the
model can say what it could not check
([_toolsets_instructions.jinja2](https://raw.githubusercontent.com/robusta-dev/holmesgpt/master/holmes/plugins/prompts/_toolsets_instructions.jinja2)).
Structured sections existed and were removed: tag 0.13.1 had
`DEFAULT_SECTIONS` = Alert Explanation, Key Findings, "Conclusions and
Possible Root causes", Next Steps, Related logs, App or Infra?, External
links, with "Don't say root cause but 'possible root causes'"
([investigation_structured_output.py @0.13.1](https://raw.githubusercontent.com/robusta-dev/holmesgpt/0.13.1/holmes/core/investigation_structured_output.py));
master has none of those files. What survived is `holmes/checks/`, a
`CheckResponse{passed: bool, rationale: str}` whose prompt says "If you
cannot verify it's healthy, it's not healthy"
([check_system_prompt.jinja2](https://raw.githubusercontent.com/robusta-dev/holmesgpt/master/holmes/checks/check_system_prompt.jinja2)).
Runbooks were YAML matched on alert name plus a markdown catalogue fetched
by the model; they are now `SKILL.md` files matched by description and
fetched with `fetch_skill`, and the docs say old runbooks "need to be
converted to the SKILL.md format"
([skills.md](https://raw.githubusercontent.com/robusta-dev/holmesgpt/master/docs/reference/skills.md),
[runbook catalog example](https://raw.githubusercontent.com/HolmesGPT/holmesgpt/master/examples/custom_runbook_catalog/README.md)).
A runbook is advice the model follows, never a code-executed pipeline. The
eval harness is `tests/llm/test_ask_holmes.py` over 274 `test_case.yaml`
fixtures with `user_prompt`, `expected_output`, `tags` (easy, hard, logs,
loki, chain-of-causation, toolset-limitation, …) and an LLM judge
([test_case_utils.py](https://raw.githubusercontent.com/robusta-dev/holmesgpt/master/tests/llm/utils/test_case_utils.py)).

**k8sgpt is the pure fixed-investigator design.** `RunAnalysis()` runs
`coreAnalyzerMap` (Pod, Deployment, Service, Ingress, Node, …) plus an
additional map concurrently under a semaphore; only then `GetAIResults()`
calls the model once per result with a prompt keyed by `analysis.Kind`,
cached by failure text ([analysis.go](https://raw.githubusercontent.com/k8sgpt-ai/k8sgpt/main/pkg/analysis/analysis.go),
[analyzer.go](https://raw.githubusercontent.com/k8sgpt-ai/k8sgpt/main/pkg/analyzer/analyzer.go)).
Every analyzer implements `Analyze(analysis Analyzer) ([]Result, error)`
and returns `Result{Kind, Name, Error []Failure{Text, Sensitive}, Details,
ParentObject}` ([types.go](https://raw.githubusercontent.com/k8sgpt-ai/k8sgpt/main/pkg/common/types.go)).
The model's interface is `GetCompletion(ctx, prompt)`, no tools
([prompts.go](https://raw.githubusercontent.com/k8sgpt-ai/k8sgpt/main/pkg/ai/prompts.go)).
That is the spec's uniform `Evidence` dict, in production, with the model
confined to explaining.

**Databricks is the production case for checks-first.** "Structured checks
before open-ended reasoning. AI SRE runs deterministic platform health
checks and runbook steps first"; "The LLM layer synthesizes and explains the
results, but the data gathering isn't left to the model's judgment"; "Every
conclusion AI SRE presents links back to the underlying evidence: the
specific metric, the log line, the deploy diff", at 2,000+ investigations a
day (vendor post, [Databricks blog](https://www.databricks.com/blog/how-databricks-uses-ai-accelerate-incident-investigation)).

**The other commercial agents describe a hybrid: a code-shaped outer loop
with model-chosen probes inside a hypothesis.** Datadog: Bits "dynamically
generates multiple root cause hypotheses and tests them", each "validated,
invalidated, or inconclusive"; early versions "made 12 generic calls",
later ones "only look at data that is causally related to a hypothesis";
the docs say it "either presents a clear, evidence-backed conclusion or
marks the investigation as inconclusive" (vendor,
[Datadog blog](https://www.datadoghq.com/blog/bits-ai-sre/),
[building Bits](https://www.datadoghq.com/blog/building-bits-ai-sre/),
[docs](https://docs.datadoghq.com/bits_ai/bits_ai_sre/)). incident.io:
start from "the alert that fired (including any error and stack trace)",
"gather context in parallel", then analyse; "A finding is a claim … Evidence
is what supports or contradicts it"; confidence runs Speculation → Pattern
match → Supported by context → Validated by system state → Validated with
alternatives ruled out; "roughly half of an investigation's work goes not
into forming a hypothesis but into challenging and proving it"
([how investigations work](https://docs.incident.io/investigations/how-investigations-work),
[overview](https://docs.incident.io/investigations/overview)). PagerDuty's
supervisor "formulates a few candidate root causes … and spawns a sub-agent
for each one", each reporting "evidence either supporting or disproving its
hypothesis" (vendor, [PagerDuty engineering](https://www.pagerduty.com/eng/inside-pagerdutys-sre-agent-how-we-built-deep-incident-investigation/)).
Google's Cloud Assist runs "deep analysis in parallel across Cloud Logs,
Cloud Asset Inventory, App Hub, Metrics, Errors, and Log Themes" into
"ranked and filtered 'Observations'", then hypotheses each with
"recommended fixes … to confirm or refute the hypothesis"
([Google Cloud blog](https://cloud.google.com/blog/products/management-tools/gemini-cloud-assist-investigations-performs-root-cause-analysis),
[docs](https://docs.cloud.google.com/cloud-assist/investigations)); Google
SRE's agents "establish domain and intent" from topology and dependency
data before forming hypotheses, and "must be able to explain … what
options were considered and rejected"
([Google SRE blog](https://cloud.google.com/blog/products/devops-sre/how-google-sre-is-using-agentic-ai-to-improve-operations)).
Resolve.ai: parallel domain agents, "ranked findings with explicit
confidence levels", "the burden of proof is high" (vendor,
[resolve.ai](https://resolve.ai/blog/how-we-built-resolve-ai)). Traversal:
a causal index tests "thousands of hypotheses in parallel", output "a
single, causally consistent diagnosis with evidence" (vendor,
[traversal.com](https://www.traversal.com/blog/introducing-causal-search-engine-from-correlated-alerts-to-causally-consistent-diagnoses)).
Cleric: "read-only by default", "confidence scores", hypotheses in parallel
(vendor, [cleric.ai](https://cleric.ai/blog/introducing-cleric)). Parity's
site refused connection; not verified.

**The measured systems put the model after a fixed gather.** RCACopilot
matches an incident to a handler by alert type, the handler is "a workflow
that consists of a series of actions" written by on-call engineers, and
only then GPT-4 predicts a category and writes the narrative: Micro-F1
0.766 on 653 incidents against 0.022 for XGBoost
([arXiv 2305.15778](https://arxiv.org/abs/2305.15778)). The open-loop
benchmarks are bleak: ITBench agents resolve 13.8% of SRE scenarios
([arXiv 2502.05352](https://arxiv.org/abs/2502.05352)); on OpenRCA "the
best-performing model, Claude 3.5, solved only 11.34%" with the purpose-built
RCA-agent ([OpenRCA](https://github.com/microsoft/OpenRCA)); AIOpsLab's best
RCA accuracy is ReAct at 45.45%, with failures like "repeatedly calling the
same API" and cat-ing logs that "overwhelm the model's input context window"
([arXiv 2501.06706](https://arxiv.org/html/2501.06706)). A 1,675-run study
finds "hallucinated data interpretation and incomplete exploration … persist
across all models regardless of capability tier"
([arXiv 2602.09937](https://arxiv.org/abs/2602.09937)).

## 3. Recurring investigator patterns and their names

- **Uniform result shape.** k8sgpt's `Result`, Holmes' `StructuredToolResult`,
  Google's "Observation" and incident.io's finding/evidence pair are the same
  idea: one record per probe, so the synthesis reads one list. The pieces
  being tested are "hypotheses" (Datadog, Google, PagerDuty, incident.io);
  what supports them is "evidence" or "signals" (Databricks, Cleric).
- **Fixed set run every time, self-skipping in code.** k8sgpt runs its whole
  map; Databricks runs "deterministic platform health checks … first";
  incident.io and Google gather from every connected source in parallel and
  gate in code on whether there is enough context to start. None lets the
  model pick which source to consult first.
- **Bounded per node, not per run.** Holmes caps each tool result at
  min(15% of context, 25k tokens) and spills the rest; Anthropic says "return
  only high signal information", expose `response_format` concise vs
  detailed, use "pagination, range selection, filtering, and/or truncation
  with sensible default parameter values", and notes Claude Code "restricts
  tool responses to 25,000 tokens by default"
  ([writing tools](https://www.anthropic.com/engineering/writing-tools-for-agents)).
  SWE-agent's ablation shows the shape matters as much as the loop: a
  summarised search scores 18.0 against 12.0 for iterative search on the same
  agent ([arXiv 2405.15793](https://arxiv.org/abs/2405.15793)).
- **Runbook = prose the model follows; check / analyzer = code that runs.**
  Holmes fetches a runbook and follows it; k8sgpt's analyzers are compiled;
  Google SRE names "playbooks" as things agents navigate. A "skill" per the
  Agent Skills spec is a `SKILL.md` loaded progressively (metadata ~100
  tokens, body under 5,000) with optional `scripts/` and `references/`
  ([specification](https://agentskills.io/specification)), which is what
  friday's skill tools already serve. No primary source names components
  "probes" or "detectors"; not verified.
- **Condensed hand-back.** Sub-agents return "only a condensed, distilled
  summary of its work (often 1,000-2,000 tokens)", and context is fetched
  "just-in-time" by lightweight identifiers
  ([context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)).

## 4. Where the reasoning goes, and what is measured

Anthropic defines workflows as "systems where LLMs and tools are
orchestrated through predefined code paths" and agents as systems where
"LLMs dynamically direct their own processes"; workflows "offer
predictability and consistency for well-defined tasks", and the rule is
"finding the simplest solution possible, and only increasing complexity when
needed"; orchestrator-workers is for "complex tasks where you can't predict
the subtasks needed" ([building effective agents](https://www.anthropic.com/engineering/building-effective-agents)).
Their research system is orchestrator-workers, beat single-agent Opus 4 by
90.2% on an internal eval at about 15× the tokens of a chat, scales effort
by tiers ("simple fact-finding requires just 1 agent with 3-10 tool calls"),
and warns that domains "that require all agents to share the same context
… are not a good fit" ([multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)).
Cognition argues for one linear agent because "actions carry implicit
decisions, and conflicting decisions carry bad results"
([don't build multi-agents](https://cognition.com/blog/dont-build-multi-agents)).
MAST annotated 1,600+ traces across 7 frameworks: system design ~44%,
inter-agent misalignment ~32%, task verification ~24% of failures; adding a
final verification step to ChatDev gave +15.6%
([arXiv 2503.13657](https://arxiv.org/abs/2503.13657)).

The numbers favour planning the gather up front and reasoning once at the
end when the gather is predictable. ReWOO's Planner → Workers → one Solver
scores 42.4% at 1,986 tokens on HotpotQA against ReAct's 40.8% at 9,795,
and its authors concede the plan degenerates to the interleaved worst case
where "the next query depends on the last answer"
([arXiv 2305.18323](https://arxiv.org/abs/2305.18323)). LLMCompiler plans a
DAG of tool calls for up to 3.7× latency, 6.7× cost and ~9% accuracy over
ReAct ([arXiv 2312.04511](https://arxiv.org/abs/2312.04511)). ADaPT
decomposes only on failure and beats both ReAct and plan-and-execute by
27–33 points on agent benchmarks ([arXiv 2311.05772](https://arxiv.org/abs/2311.05772)).
Agentless, a fixed localise → repair → validate pipeline, reached 32.00% on
SWE-bench Lite at $0.70 ([arXiv 2407.01489](https://arxiv.org/abs/2407.01489)).

For diagnosis specifically, three measurements agree that loop shape is not
what separates the systems. On SWE-bench Verified OpenHands solves 53.0%
and Agentless 50.8%; "pipeline-based tools mainly fail in localization",
"agentic tools shift the failure burden to the iteration & validation
stage", with agents stuck in "cognitive deadlocks"
([arXiv 2509.13941](https://arxiv.org/abs/2509.13941)). On Microsoft
incidents ReAct scored 35% correct against 39% for CoT and retrieval, but
hallucinated in 6% of wrong answers against 18% and 49%: interleaving buys
grounding, not correctness ([arXiv 2403.04123](https://arxiv.org/html/2403.04123)).
ReAct's own paper says the same: 6% false positives in successes against
CoT's 14%, and "non-informative search … 23% of the error cases"
([arXiv 2210.03629](https://arxiv.org/abs/2210.03629)). The one paper-grade
number for "gather cheap evidence first, then reason once" on RCA is
DiagGuard's 43.5% → 52.5% from grounding in observations before localising
and checking conclusions against evidence after
([arXiv 2608.21310](https://arxiv.org/abs/2608.21310)).

## What this means for friday's `api_issue` graph

**The design is k8sgpt's shape with HolmesGPT's prompt, and that is the
best-supported pair.** A fixed set of self-skipping checks, one uniform
result each, then one model call: that is `coreAnalyzerMap` → `Result[]` →
`GetAIResults`, Databricks' "structured checks before open-ended
reasoning", and RCACopilot's handler → diagnostics → LLM, the one incident
design with years of production and a measured accuracy. `conclusive` and
`not_checked` as fields of `Diagnosis` are what Holmes enforces by prompt
and what Datadog and Holmes' `checks/` make first-class; a field is
stronger than a prompt.

**Keep:**
- One reasoning node after the gather. ReWOO, LLMCompiler, Agentless,
  RCACopilot and DiagGuard all favour it when the gather is known, and the
  spec's node list is known. The Liu et al. and Roy et al. numbers say the
  interleaved loop would not buy correctness here.
- The uniform `Evidence` dict, named after k8sgpt and incident.io: `source`
  (which check), `found`, `lines` (verbatim), `query` (what was asked, so
  "not checked" can say it), `window`, `truncated`, `skipped_reason`. Store
  as JSON, as the spec already requires.
- Self-skipping by rule, not by model. Databricks, incident.io and Google
  gate in code; k8sgpt filters in code.
- Idempotent `notify`, and re-run from `route` on new parameters: LangGraph
  and ADK both document exactly this.
- Every claim in the `Diagnosis` pointing at a line in `evidence`. That is
  incident.io's finding/evidence pair, Databricks' "every conclusion links
  back", and the failure mode OpenRCA 2.0 and the 1,675-run study say
  dominates: ungrounded claims and incomplete exploration.

**Rename to match common vocabulary:**
- The investigator nodes are *checks* (Databricks, incident.io, Holmes
  `checks/`; k8sgpt says *analyzers*); the family is `checks`, the result a
  `Finding` or `Evidence`. `RecallMemory` is not a check; it is context
  injection, the way the extractor already receives domain memories.
- `conclusive: bool` is Holmes' `CheckResponse.passed` and a flat version of
  Datadog's `validated | invalidated | inconclusive`; fine for one
  hypothesis, and the three-way word is there if a second is ever added.
- What ticket 07 asks the operator to write is a *runbook* in Holmes' old
  sense and a *skill* in its new one: prose the model follows, matched by
  description, beside the structured routing table code reads. Use those
  words.

**What the surveyed systems have that the design does not yet:**
- **A per-check result budget.** Holmes caps every tool result and spills
  the rest; Claude Code caps at 25,000 tokens; AIOpsLab names unbounded log
  reads as a top failure. Node 0's `extraction_budget_tokens` covers the
  transcript; nothing in the spec bounds what `logs_prod` hands `diagnose`,
  and the spec's own 780 KB `loki_series` measurement says the risk is
  real. Add `max_lines` per check, recorded on the `Evidence` as
  `truncated` so "not checked" can name it.
- **A scoring harness.** Holmes ships 274 tagged fixtures with an LLM
  judge; RCACopilot, ITBench, OpenRCA and Datadog's eval platform all score
  against labelled past incidents. The spec has the report file but no set
  of past cases with a known cause to score `Diagnosis` against, the way
  `evals/triage.jsonl` scores triage. D16's baseline-first rule applies, and
  the cases are the operator's to label.
- **A written decision about the hypothesis loop.** Datadog, incident.io
  and PagerDuty iterate: critique, ask a narrower question, re-check;
  incident.io says half the work is challenging the hypothesis. The spec has
  one `diagnose` and one `conclude`, which is the Agentless and Databricks
  bet, and the numbers support it. It should be recorded as a decision with
  the escape hatch named: the D8 escalation predicate is the one place a
  second gather happens, gated by rule, which is what ADaPT measured as the
  best middle position.
