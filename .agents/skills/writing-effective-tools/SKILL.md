---
name: writing-effective-tools
description: Design and evaluate tools built for AI agents rather than for human developers or deterministic callers — consolidating APIs into higher-level tools instead of wrapping every endpoint one-to-one, namespacing for tool-selection clarity, shaping responses for signal over completeness, token-efficient pagination and truncation, and writing tool descriptions like onboarding docs for a new hire. Use this whenever adding, reviewing, or refactoring a tool an agent calls (including anything under friday/tools/), designing a tool's parameter schema or docstring, debugging an agent that keeps calling the wrong tool or the right tool wrong, or judging whether a set of similar-looking tools should be consolidated into one.
---

# Writing Effective Tools for Agents

Anthropic's engineering post on designing tools built for agents specifically (`anthropic.com/engineering/writing-tools-for-agents`, Sept 2025). Its framing: a tool definition is **"a contract between deterministic systems and non-deterministic agents"** — unlike a function signature between two programs, an agent given a `get_weather` tool might call it, answer from general knowledge, or ask a clarifying question first. The goal is to increase the surface area over which an agent can act effectively, not to expose the most functionality.

## Choose the right tools — don't just wrap the API

Not every endpoint should become a tool one-for-one. Prefer `search_contacts` over `list_contacts` — an agent has limited context to page through results a human would just scroll. Consolidate multi-step workflows (`read_logs` / `list_users` / `create_event`) into fewer, higher-level tools rather than exposing every primitive separately: agents pay a real selection cost for every additional tool that overlaps with another in purpose.

**The consolidation test that actually matters**: are the "duplicate-looking" tools really interchangeable ways to do the same lookup, or do they split along an axis that has nothing to do with the operation — like what the caller already knows, or how much it should pay? Four tools that split cleanly by *input the caller has* vs. *input it doesn't* aren't the anti-pattern this rule warns about, even though they look like four tools where a naive reading says there should be one. Read what each tool actually requires before applying the "fewer tools" rule.

## Namespace for disambiguation

Prefixed, service-scoped names — `asana_projects_search`, `asana_users_search` — let an agent tell apart similarly-shaped tools from different systems, and encode structure in the name itself rather than making the model infer it from a description at call time. This is about what the **model sees in the tool name string**, not just how the code is organized on disk — a well-organized module of tools whose names don't carry the same distinguishing information hasn't actually gotten this benefit.

## Shape responses for signal, not completeness

Favor human-readable, high-signal fields over technical identifiers (UUIDs, raw internal IDs) unless the next call actually needs the identifier. The concrete mechanism Anthropic reports: a `ResponseFormat` enum letting the *agent* request `concise` vs. `detailed` output — measured to cut token consumption by roughly two-thirds on the concise path, without giving up the ability to fetch full detail when it's actually needed. Pagination, filtering, range selection, and truncation-with-sensible-defaults are required tool behaviors, not optional polish — and truncation specifically should come with instructions steering the agent toward a more efficient next call, not just silently drop data.

## Write the description like onboarding docs

Anthropic's own framing: **"describe your tool to a new hire."** Implicit domain knowledge — query syntax, vocabulary, how resources relate to each other — has to be made explicit in the description, because the model has no other channel to learn it. This isn't a nice-to-have: Anthropic cites a measured case where refining tool descriptions alone produced a documented accuracy gain on SWE-bench Verified, with no change to the underlying tool at all. Error messages deserve the same treatment — name the specific problem and a corrective action, never an opaque code.

## Evaluate and iterate with real, multi-tool tasks

Ground eval tasks in genuine workflows ("schedule a meeting with document attachments," "correlate a customer issue across several data sources") that require several tool calls, not one. What a trajectory's response *omits* is often more diagnostic than what it includes when a tool call goes wrong. The most effective iteration loop in the source isn't a human alone tuning a description by hand — it's prototyping a tool, running it against real tasks, then handing the transcripts back to an agent to analyze the failures and refine the tool definition itself; Anthropic reports this outperforming hand-tuned internal tools built by humans working alone.

Concrete checklist and response-shaping examples in [`references/tool-design-checklist.md`](references/tool-design-checklist.md). Full citations: `docs/research/agentic-system-design/writing-effective-tools.md`.

## Applying this to Friday

**Description as single source of truth**: triage's `Decided` enum description is generated per task type from each `Params` dataclass's own docstring, and a class with no real docstring is refused at import — a stronger version of "describe it like a new hire," because it makes the description structurally impossible to drift from the type it describes.

**The skill-tools split (`fetch_skill` / `search_skills` / `describe_skill` / `read_skill_file`) is not the anti-pattern it looks like at a glance.** It's four tools, which the consolidation rule above would flag on sight — but the split is by what the calling agent already knows (a skill's name vs. no name) and how much it should pay for the answer (metadata vs. full body), closer to the source's own `ResponseFormat` concise/detailed pattern than to near-duplicate CRUD verbs on one resource. Apply the consolidation test above before recommending a merge here.

**`harness.tool`'s `failure_error_function`** — returning a plain string instead of letting a failing tool body's raw exception reach the model — is response-shaping in the source's sense (bounding what reaches the agent's context, avoiding a leaked path or credential), even though Friday's own stated motive (avoiding a blind retry that double-writes) is a different, additional concern the source doesn't name. Both arguments point the same direction; know that they're not the same argument if someone asks why this exists.
