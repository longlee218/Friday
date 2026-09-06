# Spec: Discord mention triage with agentic procedures

Status: ready for implementation, except the two procedures (see Out of Scope).
Design rationale and rejected alternatives live in `docs/DESIGN.md`.

## Problem Statement

I get tagged in Discord constantly — directly, through my `@backend` role, and
in DMs. Most of it is noise. Some of it is a bug report, or a request to trace
something, and those need real work that I do by hand every time: read the
thread, work out what is actually being asked, go look at logs or code, come
back, and reply.

Three things go wrong today. Messages get lost — a mention scrolls past while I
am heads-down and I never see it. The work is repetitive — the first twenty
minutes of every bug report is the same mechanical evidence-gathering. And I
have no view of what is outstanding, so I cannot tell what I have already
handled from what is still waiting on me.

## Solution

A service that watches the channels I care about, classifies every message that
mentions me, discards the small talk, and turns the rest into tasks. Each task
runs a fixed workflow whose steps are LLM nodes with tools — they gather the
evidence and draft the response. Nothing reaches the channel without my
approval, which I give from Discord with a button. A web page shows me every
task and what state it is in.

The workflow skeleton is fixed and persisted; only the reasoning inside each
step is open-ended. That is what makes the system auditable and restartable.

## User Stories

### Never missing a message

1. As the mentioned human, I want every direct `@me` in a watched channel captured, so that nothing depends on me scrolling back.
2. As the mentioned human, I want `@backend` role mentions captured too, so that team-wide escalations reach the same pipeline.
3. As the mentioned human, I want direct messages captured, so that private escalations are not a blind spot.
4. As the mentioned human, I want capture limited to a channel whitelist, so that unrelated servers do not generate work or cost.
5. As the mentioned human, I want messages that arrive while the connection is down to be recovered afterwards, so that an internet blip does not silently lose a bug report.
6. As the mentioned human, I want a message delivered by both the live connection and the recovery sweep to produce exactly one task, so that I never see duplicates.
7. As the mentioned human, I want the recovery sweep to run on a timer as well as on reconnect, so that a connection that is up but stalled is still covered.
8. As the mentioned human, I want the service to remember where it left off across restarts, so that a deploy does not create a gap.
9. As the mentioned human, I want to be notified if the connection has been down for more than a few minutes, so that a dead service does not look like a quiet day.
10. As the mentioned human, I want a daily summary of how many mentions were processed, so that I can sanity-check that it is alive and working.

### Understanding what was said

11. As the mentioned human, I want each captured message classified as a bug report, a tracing request, or noise, so that only real work becomes a task.
12. As the mentioned human, I want classification to read the surrounding thread, so that a follow-up like "still broken" is understood in context.
13. As the mentioned human, I want noise recorded but not actioned, so that I can later audit what the system chose to ignore.
14. As the mentioned human, I want low-confidence classifications escalated to me rather than guessed, so that the system fails toward asking.
15. As the mentioned human, I want a follow-up in a thread with an open task to update that task, so that one conversation does not fragment into five tasks.
16. As the mentioned human, I want a follow-up that changes the classification to be escalated, so that a bug report that turns into something else is not silently relabelled.
17. As the mentioned human, I want a classification failure, timeout, or malformed response to produce a task for me, so that a message is never dropped because the model misbehaved.
18. As the maintainer, I want every classification decision logged with its confidence score, so that I can set the threshold from real data instead of guessing.

### Getting the work done

19. As the mentioned human, I want each task to run a fixed sequence of steps, so that I can tell exactly which step failed when something goes wrong.
20. As the mentioned human, I want each step to be able to reason and call tools, so that it can actually investigate rather than follow a rigid script.
21. As the mentioned human, I want each step's result persisted before the next one starts, so that a restart resumes rather than redoing work.
22. As the mentioned human, I want a step that needs my input to pause and wait, so that the system can ask rather than guess.
23. As the mentioned human, I want a paused task to survive a restart, so that a deploy does not lose work that is waiting on me.
24. As the mentioned human, I want a step that loops too long to stop and ask me, so that a stuck step cannot burn budget indefinitely.
25. As the mentioned human, I want a step that spends too many tokens to stop and ask me, so that cost has a hard ceiling.
26. As the mentioned human, I want a model refusal treated as a request for help rather than a crash, so that it lands in my queue like any other blocker.
27. As the mentioned human, I want a failing tool to report honestly to the step, so that the step does not conclude "there were no logs" when the log service was simply down.
28. As the mentioned human, I want transient tool failures retried automatically, so that a network blip does not consume a step's turn budget.
29. As the maintainer, I want each step's tool calls and results retained for diagnosis, so that I can answer "why did it conclude that?".
30. As the maintainer, I want downstream steps to depend only on a declared output schema, so that steps stay independently replaceable.
31. As the maintainer, I want a configurable number of tasks running at once, so that I can tune throughput against rate limits and spend.

### Staying in control

32. As the mentioned human, I want nothing posted publicly without my approval, so that a misclassification cannot embarrass me in front of my team.
33. As the mentioned human, I want to approve or reject from Discord with a button, so that I can clear the queue from my phone.
34. As the mentioned human, I want the approval request to arrive as a direct message, so that it reaches me where I already am.
35. As the mentioned human, I want the approval prompt to show what would be posted, so that I am approving specific text rather than a vague intention.
36. As the mentioned human, I want a record of who approved what and when, so that there is an audit trail.
37. As the mentioned human, I want replies to appear as coming from me, so that the person who tagged me gets a reply from me.
38. As the mentioned human, I want a step other than the reply step to be unable to post, so that only approved output reaches a channel.

### Seeing the state

39. As the mentioned human, I want a web page showing every task grouped by state, so that I can see at a glance what is outstanding.
40. As the mentioned human, I want the page to update without a manual refresh, so that it is usable as a live dashboard.
41. As the mentioned human, I want the page to show connection status and the time of the last event, so that I can tell whether ingestion is healthy.
42. As the mentioned human, I want the page to be read-only, so that there is exactly one place where decisions are made.

> **39–42 amended 2026-09-06** by `.scratch/a-window-on-the-whole-path/`. The
> page that satisfied 39 and 41 was deleted and is being rebuilt in `web/`; 40
> is still unmet and still polls (ticket 20 of `discord-mention-triage`, SSE,
> remains open).
>
> **42 is narrowed, not withdrawn.** "Exactly one place where decisions are
> made" still holds and is still enforced: task state, approvals and
> classifications are not editable from a browser, and the tools that produce
> them are Discord's. What changed is that a channel's context `overrides` —
> the layer of `context/<channel>.yaml` the machine never writes and which was
> created for the operator — is now editable from the page. That is context,
> not a decision. The argument is D7 on the new board; `check_exposure`
> tightening (D10) is its consequence, because "unauthenticated is safe
> because it is read-only" was always an argument about writes.
>
> Two stories are added there rather than here, since they are that board's:
> seeing the whole path one message took through the process, and seeing what
> a task reached for and what it cost.

### Learning over time

43. As the mentioned human, I want the system to accumulate notes on recurring problems and my preferred tone, so that later responses need less correction.
44. As the mentioned human, I want a step to be able to record an observation during a task, so that context is not lost when the step ends.
45. As the mentioned human, I want observations promoted to long-term notes only when the task they came from was approved, so that a wrong guess does not become a permanent belief.
46. As the mentioned human, I want long-term notes to stay small and be rewritten rather than appended, so that they do not grow without bound.
47. As the maintainer, I want long-term notes typed by category, so that different kinds of note can have different promotion rules.

### Operating it

48. As the maintainer, I want structured configuration in a version-controlled file, so that changing a model or a limit is a reviewable change.
49. As the maintainer, I want secrets kept out of that file, so that configuration can be committed safely.
50. As the maintainer, I want the account token kept out of logs and stored data, so that an unhandled exception cannot leak it.
51. As the maintainer, I want the whole thing to run as one container against a persistent volume, so that deployment is a single unit and restarts are safe.
52. As the maintainer, I want each step's model chosen independently, so that I can tune cost and quality per step.

## Implementation Decisions

**Ingestion is one module with one method.** Its entire interface is an async
iterator of normalised inbound events. Live socket handling, session resumption,
the periodic REST recovery sweep, per-channel cursors, mention filtering,
whitelist scoping, and deduplication are all implementation. Callers cannot
observe which delivery path produced an event.

**Deduplication is keyed on `(provider, provider_message_id)`** and every
handler downstream of it must be idempotent on that key. This is what allows
the two delivery paths to run concurrently and the sweep to run aggressively.

**Resume state is persisted, not held in memory** — the session identifier and
sequence number for the live connection, and the last-seen message id per
watched channel.

**Two platform identities, one process.** The bot identity handles approval
prompts and their interactions; the user identity handles ingestion and
outbound replies. They are separate modules; the user-side is isolated because
it depends on an unofficial interface and is expected to break.

**A single `Provider` interface** covers inbound normalisation, reply, and
approval requests. Platform-specific mechanics (interactive components,
interaction payloads, correlation of an answer back to a task) sit entirely in
the implementation.

**Every unit of reasoning is a node**, declared as a frozen record: name,
prompt, tool subset, output schema, model, turn cap, token cap, refusal
behaviour, and whether it may pause. Classification is itself a node with no
tools and a one-turn cap, so there is one runtime and one code path.

**A procedure is data, not a coroutine** — an ordered list of nodes. The runner
persists each node's result before advancing; resume means skipping completed
nodes. A node that needs a human *returns* a pause value rather than awaiting
one. This is the decision that makes a task waiting on a human survivable
across a restart, since a suspended coroutine cannot be persisted.

**Node checkpoints hold two things with different contracts**: the structured
output, which is the only thing downstream nodes may read, and a trimmed
transcript of tool calls and results, which is diagnostics that nothing
downstream may read. Trimming happens at node end; inside the loop every output
item is replayed unchanged as the API requires.

**The agent loop is scoped to one node.** It never spans nodes. Resume
granularity is therefore the node, which is the argument for keeping nodes
small.

**Three abnormal exits, all routed to the human-input state**: turn cap
exceeded, token cap exceeded, and model refusal. Refusal is not an error
condition — it arrives as a successful response whose message content carries a
refusal part, so it must be checked for explicitly rather than caught.

**Conversation state is held client-side**, not stored by the model provider,
consistent with the local database being the single source of truth.

**Prompts are assembled stable-first**: instructions, long-term notes, and tool
definitions, then prior node outputs, then the triggering message and thread
context. Anything after the volatile section cannot be cached.

**The tool registry is shared across all nodes**, so scope does not constrain
side effects. The guard therefore lives inside the side-effecting tool itself:
the reply tool verifies the task has been approved and returns a refusal as a
normal tool result rather than raising, so the model can adapt.

**Memory is two-tier.** A record-observation tool writes to a staging tier
scoped to the current task, supplying only a category and text; the runner
attaches provenance. A separate compaction pass promotes to the long-term store
only what an approved outcome corroborates, and rewrites rather than appends.
Categories are a closed set so promotion rules can differ per category.

**Task lifecycle is expressed as transitions, not as a writable state field.**
Callers move tasks with verbs; the legal state graph lives in one place.
Claiming on startup returns tasks stranded mid-flight, which is how a restart
heals.

```
pending → processing → HITL → processing        (human answered)
                     → review → done            (approved)
                             → HITL             (rejected)
```

**Storage is a single embedded database file on a persistent volume**, accessed
asynchronously — a blocking database call on the event loop would stall
ingestion, which is the exact failure the recovery layer exists to prevent.

**Concurrency is a configurable bounded pool** over tasks.

**Configuration splits in two**: structured settings (pool size, per-node models
and caps, channel whitelist, thresholds, memory caps) in a version-controlled
file; secrets in an environment file, never committed and never logged.

**Everything runs as one process in one container.** This follows from the
storage choice: multiple writers to one file over a shared volume produces
contention and locking failures.

## Testing Decisions

**A good test here drives the system from outside and asserts on observable
outcomes** — which tasks exist, what state they are in, what was sent outward.
It does not reach into deduplication internals, assert on prompt strings, or
verify that a particular private function was called. If a test needs to look
past the seam to be meaningful, the module is the wrong shape.

**Two seams, both new** (the repository has none today):

1. **The `Provider` interface.** A fake implementation feeds raw platform
   payloads in and records outbound calls. The overwhelming majority of tests
   go through this one seam: given these messages arrive, assert the resulting
   tasks, states, and outbound effects. Ingestion, classification, the node
   runner, task transitions, tool guards, and memory staging are all exercised
   through it without being addressed directly.

2. **The model transport.** Scripted responses stand in for the provider,
   because the agent loop is nondeterministic and costs money per run. Tests
   script tool-call sequences, refusals, and terminal outputs.

Time is injected rather than mocked globally, so recovery-sweep intervals and
liveness thresholds are testable without sleeping.

**What gets tested:**

- Ingestion, through the provider seam: the same message delivered by both
  paths yields exactly one event; a gap during disconnection is recovered;
  cursors and resume state survive a restart; non-whitelisted channels produce
  nothing.
- Classification, as a fixture set of real messages with expected labels. This
  is the regression net for prompt changes — the failure it must catch is a
  prompt edit that turns follow-ups into duplicate tasks.
- Task transitions: every illegal transition raises; claiming on startup
  recovers stranded tasks.
- The node runner: turn cap, token cap, and refusal each land in the
  human-input state; a pause is persisted and resumed at the right node; a
  completed node is skipped on re-run.
- The tool guard: the reply tool refuses when the task is not approved,
  regardless of which node calls it, and the refusal comes back as a tool
  result rather than an exception.
- Memory: staged observations are not visible in long-term notes until
  promoted; promotion only occurs for approved outcomes.

**No prior art exists** — there are no tests in the repository yet. These
establish the conventions.

## Out of Scope

**The two procedures themselves.** Their node lists, prompts, tool sets, output
schemas, models, and cap values are undefined. What is in scope is the runner
that executes them and the registry they plug into; the procedures ship as empty
node lists, and the runner is tested against a fake procedure.

**Investigation tools** — anything that reads logs, traces, repositories, or
databases. These depend entirely on the procedures above, including what the
agent authenticates to and whether the deployment target can reach those
systems at all. That last point may force a change of deployment location.

**Additional platforms.** Slack and Telegram are anticipated in the interface
design but no second implementation is built here.

**Plugin registration for tools.** Tools are built in for now; dynamic
registration is future work.

**Long-term note retrieval.** The whole long-term store goes into every prompt;
selecting a relevant subset is future work and needs a corpus first.

**Cross-task deduplication beyond thread linking.** Follow-ups link to open
tasks in the same session; recognising that two separate threads describe the
same underlying issue is out of scope.

**Any *decision* on the web page.** It displays state; decisions happen in
Discord. Task state, approvals and classifications are not editable there, and
the agent's own memory is not either.

> Amended 2026-09-06. This read "Any interaction on the web page", and one
> interaction now exists: editing a channel's context `overrides`, the layer of
> `context/<channel>.yaml` the machine never touches. The distinction the
> original sentence did not need to draw is between a decision and a context —
> the rule protects "exactly one place where decisions are made" (story 42),
> and what is true about a room is not a decision. See D7 in
> `.scratch/a-window-on-the-whole-path/SPEC.md` for the full argument, and D10
> for why `check_exposure` had to tighten as a result.

## Further Notes

**The classification threshold and the per-node caps are deliberately unset.**
There is no accuracy data yet, so any number chosen now would be invented. Start
the threshold high, log every scored decision, and derive the value from the
observed distribution. Expect the human-input queue to be busy at first — that
is the system working, not failing.

**The account being automated is the system's single point of failure.**
Automating a user account is against the platform's terms of service and risks
termination; a long-lived authenticated connection is the documented primary
signal. This was decided deliberately after being raised. The practical
consequence for implementation is that the user-side module should stay small
and replaceable, and its credential must never reach logs, tracebacks, or
stored data.

**The unofficial client library tracks a private interface** and can break
without notice. Isolation is the mitigation.

**Every classified message costs a model call, including the ones discarded as
noise.** Cost scales with mention volume, not with task volume. The threshold
and the channel whitelist are the levers.

**Suggested build order**, so that each stage is verifiable before the next
depends on it: ingestion and persistence first, so real messages can be watched
landing in storage before any model call exists; then the node runtime with
classification as its first node, logging scores without acting on them; then
transitions, the page, and approvals; then the procedures once specified; then
memory promotion, once there are completed tasks to compact.
