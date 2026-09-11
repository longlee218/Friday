# friday-agents — Design

Status: **built, and this file has drifted from it.**

Read `CLAUDE.md` first — it describes what exists. This file records the
reasoning behind decisions, and several of those decisions were later reversed
in ways it does not reflect. Three are marked inline below and are the ones
most likely to mislead: the "agentic nodes" vocabulary was removed, triage's
tool schemas no longer take any parameter but `confidence`, and the memory
design below — a staging tier promoted by an approval-gated compaction pass —
was replaced by an agent writing and reading its own memory directly (ticket
09's D9, 2026-09-06).

Kept rather than rewritten because the argument is still worth reading even
where the conclusion moved. **Where it disagrees with the code, the code is
right**, and this line is the warning that it can.

A Discord agent that watches for mentions of a specific human, classifies them,
turns the actionable ones into tasks, runs a fixed workflow of agentic nodes,
and posts the result back as that human.

```
Discord mention → normalise → Triage (node) → task → node pipeline → review → reply as you
```

The shape is **a hard workflow with agentic nodes**: the skeleton is fixed and
persisted, and each node is an LLM loop with tools. The skeleton buys
auditability and restart-safety; the node buys adaptability inside one bounded
step.

> **Reversed.** The node vocabulary was dropped. Workflows are deterministic
> Python and a graph's *shape* is code — a model never chooses the next step.
> Only some nodes call a model at all. See `CLAUDE.md` § Architecture
> constraints and `CONTEXT.md` § Graph.

## Ingestion

**Two identities, one process.** `discord.py` drives the bot; `discord-self`
(namespaced `discord_self`, so both import cleanly side by side) drives the user
account. Separate modules — the user-side is deliberately rip-out-able.

**Triggers:** direct `@you`, `@backend` role mention, and DMs.
**Scope:** an explicit channel whitelist, plus all DMs.

**Two delivery paths.** Discord has no inbound webhook for messages — delivery
is gateway-websocket-only — so redundancy is REST-based:

1. Gateway with RESUME. The library owns the session id and sequence number
   in memory and reconnects on its own; neither is persisted. After a process
   restart Discord will not resume a stale session anyway, and the cursor plus
   the sweep close that gap for less code.
2. REST backfill sweep every ~5 min:
   `GET /channels/{id}/messages?after={last_seen_id}` per watched channel.

Both feed the same pipeline. **Dedup on `(provider, provider_message_id)`**
makes double-delivery a no-op. Path 2 exists because RESUME only replays if you
reconnect before the replay buffer overfills; past that you get Opcode 9 Invalid
Session and the gap is gone.

Messages authored by the watched account are dropped at the provider, so the
agent can never react to its own replies. `capture_own_messages` disables that
for testing; it must be off in normal operation, since an agent that answers
itself has no natural stopping point.

**Cursors** record how far each watched channel has been read. They advance on
every message *seen*, not every message kept, so the sweep does not re-fetch
traffic already dropped — and they only ever move forward, because the sweep
replays old messages after newer live ones.

**Known ingestion gaps, accepted deliberately:**

- **One-to-one DMs are gateway-only.** They are watched but not listed in
  `watched_channels`, so there is no list for the sweep to iterate. Enumerating
  DM channels every few minutes is a lot of API traffic for a rarer path, so a
  DM sent during an outage can be lost.
- **Threads are not swept.** A thread message reports its parent as the channel,
  and the parent's history does not contain it. Messages sent in a thread during
  an outage can be lost.

Both are recoverable later by adding sweep targets; neither is worth the traffic
today.

**Two classes of connection failure, handled differently:**

- **Transient** — dropped network, unexpected close, server error. Reconnect
  with backoff, resume, then sweep to close the gap.
- **Fatal** — close code **4004, authentication failed**, meaning the account
  credential is no longer valid. Stop reconnecting and alert. Retrying is
  pointless: only a human pasting a new credential fixes it.

Collapsing these into one retry loop is the most dangerous bug available here.
A process stuck retrying a dead credential is alive, logging "reconnecting",
and receiving nothing — indistinguishable from a quiet week.

The credential dies on a **security event, not a timer**: a password change or a
2FA toggle invalidates every session immediately. A plain logout does not. So
there is nothing to refresh on a schedule and no expiry to pre-empt — the only
correct behaviour is to detect rejection and escalate.

## Provider abstraction

An `InboundEvent` dataclass and a bidirectional `Provider` protocol with
`reply()` and `request_approval()`. The Discord implementation owns button
components and interaction payloads. Slack and Telegram are anticipated.

## Data model

SQLite, WAL mode, on a named Docker volume. Access is **async** (`aiosqlite` or
a thread executor) — a blocking DB call on the event loop stalls the Discord
gateways, which is the dropped-socket failure this design works hard to avoid.

Two kinds of storage share the file and must not be conflated. **Application
tables** hold this project's domain. **Agent session tables** hold conversation
history in the shape the Agents SDK's `Session` protocol expects — a transcript
of an agent's own turns, which is a different thing from a channel transcript of
many humans.

| Table | Key | Holds |
| --- | --- | --- |
| `messages` | unique `(provider, provider_message_id)` | every message seen. A non-null `mention_type` marks the ones addressed to us — that column, not a second table, is the triage queue. Carries the triage decision: type, confidence, parameters |
| `conversations` | `(provider, channel_id, thread_id)` | one exchange on one platform |
| `tasks` | → conversation | work items, their state, and their approval |
| `outbox` | → task | outbound intents: conversation, text, sender, reply_to, kind, attempts, last_error |
| `llm_calls` | → agent run | prompt, output, tool calls and tokens per model call, for the debug view. Trimmed on a retention bound |
| ~~`memory_staging`~~ | → task | **removed** — see the Memory section below |

`events` and `messages` were separate tables and are now one. Every in-scope
mention was written to both, so a column added to one silently went missing from
the other — which is exactly how `is_own` came to disagree with itself.

Per-channel cursors (`last_seen_message_id`) live in the same DB, so a
container restart resumes instead of cold-starting. Gateway session state is
deliberately *not* persisted — see Ingestion.

## Layers

```
Intake  →  Triage agent  →  Workflow  →  Responder agent
(built)    type, confidence   deterministic   writes in the
           and parameters     Python          operator's voice
```

Only two steps use a model. The control flow between them is ordinary code.

## LLM runtime

**The OpenAI Agents SDK** (`openai-agents`), driven through the **Chat
Completions** API rather than Responses. Chat Completions is the de-facto
standard that other providers implement, so `base_url`, `api_key` and `model`
are configuration — DeepSeek, MiniMax or anything else OpenAI-compatible can be
swapped in without touching code.

```python
client = AsyncOpenAI(base_url=..., api_key=...)
Agent(model=OpenAIChatCompletionsModel(model=..., openai_client=client))
```

**Tracing must be disabled** (`set_tracing_disabled(True)`). It is on by
default and exports to OpenAI using the same key as model requests — with a
third-party provider that leaks both the traffic and the credential.

**A known compatibility risk:** the SDK sends `response_format: json_schema`
for structured output, and some OpenAI-compatible providers reject it with a
400. This is why triage expresses its result as a **tool call** rather than a
structured output type — tool calling is the better-supported surface. Verify
against the chosen provider before relying on either.

**Verified, 2026-09-11, and the risk was the wrong one to worry about.** The
instruction above went unfollowed for a year: ticket 04 probed the provider
for *tool calling* and concluded the `response_format` risk "does not apply,
because the union is expressed as tools" — which avoided the question rather
than answering it. Probed properly now against MiniMax-M3:

- It **accepts** `response_format: {"type": "json_schema", "strict": true}`.
  No 400. The failure this paragraph was written to avoid does not happen.
- It **ignores** it. The reply came back inside a ```json fence, after a
  `<think>` block, with prose following, naming an enum member that was not
  in the enum it had just been given.

So the real hazard is the opposite shape of the one feared: a provider that
rejects the parameter is one you find out about immediately; one that accepts
and ignores it leaves a schema in the code that reads like a guarantee and
enforces nothing. **Neither surface constrains this provider** — tool calling
is not enforced on the wire either, and what makes the tool path safe is that
the SDK validates arguments *client-side* with pydantic and hands a malformed
call back for one retry.

That is the mechanism generalised in `friday/agent/structured.py` and
`Harness.run_structured`: the shape is a dataclass, it is described to the
model in the prompt, the answer is validated in this process, and an answer
that does not fit earns exactly one correction turn. Nothing is sent on the
wire, because sending it buys nothing here and costs a false sense of safety.

## Triage

One model call per mention. It decides three things and performs no I/O:

- **type** — `api_issue | access_request | doc_question | skip`
- **confidence**
- **parameters** — what the message actually contained

The type-plus-parameters pair is expressed as **one tool per type**, which is
how a discriminated union is encoded here: each tool's schema declares the
parameters its own type needs, and the model picks one.

```
create_api_issue_task(confidence)
create_access_request_task(confidence)
create_doc_question_task(confidence)
skip(confidence)
```

> **Reversed.** Every one of these took the parameters above until triage was
> cut back to classifying. Lifting values out of a message is a different job
> with a different failure mode; it belongs to `friday/extraction/`, one
> extractor per task type. A test now fails if a triage tool asks for anything
> but `confidence`, so the schemas written above are the ones the code forbids.

`tool_use_behavior="stop_on_first_tool"` ends the run on the first call, so this
is a single turn with no loop.

**The tool reports the decision; it does not act on it.** Triage stays pure, so
it is testable with no database — the caller applies the outcome.

**Parameters matter more than the type.** The most common real action is not
diagnosis, it is noticing a report is incomplete and asking for what is missing:

> *"Which environment are you using? Could you give me the CURL or the
> correlationId?"*

That is mechanical, high-frequency, and cannot be embarrassingly wrong. An
`api_issue` with no `correlation_id` and no `curl` takes that path; one with
them goes to tracing. Same type, different action, decided by parameters.

**Never-drop is enforced through the SDK's `error_handlers`**, which recover
from `max_turns`, `model_refusal` and `invalid_final_output` by returning a
value instead of raising. Each maps to a task needing human input.

**Triage runs off a queue, not inline.** Events are already persisted, so a
separate task picks up untriaged ones. A model call inside the ingest loop would
stall the gateway consumer for its duration — the exact failure the recovery
layer exists to prevent, self-inflicted.

**Categories are not all model decisions.** Salary, off-topic and social talk
are filtered to `skip` before the model sees them. A rule that important should
not depend on a classifier having a good day.

## Steps and state

A workflow is an ordered set of named steps. A step takes the state so far, does
one thing, and hands back what it learned; its result is recorded before the next
begins, so a restart continues at the first unfinished step rather than paying
for the finished ones again.

Built here rather than adopted. A graph library was measured at twenty-two extra
packages, a second HTTP client in the same container, and two of its own tables
in the one SQLite file, for a feature that is about a hundred lines. **Revisit
when durable resume spreads past two workflows** — below that the library costs
more than it saves.

Branching and parallelism are deliberately not part of it: `if` and
`asyncio.gather` already express both, and a graph that exists to replace them
puts a language between the author and their own code.

The hard part is neither the state nor the ordering. **A step with a side effect
can crash after causing it and before recording that it did**, and resuming then
does it twice. Within this system's own store, a step's result and its effect
commit together or not at all. Once something has left the process, only an
idempotency key helps — the same choice the outbox already makes, for the same
reason.

## Promoting a workflow

A workflow starts deterministic. It is promoted to something agentic **per task
type, on evidence** — when the deterministic version has proven itself and the
thing it cannot do is judgement rather than a missing branch.

The seam is usually the same: fetching is deterministic, reading is not. A query
by identifier has one right answer; deciding what a hundred results mean does
not. So the fetch is a tool with a filter on it, and the reading is the agent.

Two things follow, and they are why this is written down rather than left to
whoever builds the first one. **Volume is the risk, not correctness** — an
external store is unbounded and a context window is not, so what comes back is
bounded before it is read and truncation is visible when it bites. And **a
read-only act is enforced beside the server, not asked for in the prompt**: a
tool filter is a guarantee, an instruction is a request.

The procedures themselves — which store, which tools, which query — are the
operator's, assembled from these parts rather than designed here.

## Workflows

One workflow per type, **deterministic Python** — branching, not reasoning:

```
api_issue:
    missing correlation_id and curl  → ask for them
    otherwise                        → trace, then answer
```

Agentic workflows are a later step, taken per type once the deterministic one is
proven. Starting deterministic means the behaviour is inspectable, cheap, and
identical every time, which is what makes the first weeks of logs worth reading.

## Responder

The second agent. It writes replies in the operator's voice, learning from
**few-shot examples of their real past replies** rather than a written style
guide — real examples carry tone that description does not.

This requires the operator's own messages to be retained as conversation
context. They are still skipped as *triggers* (otherwise the agent answers
itself), but skipping them as *context* would leave every stored conversation
missing one side of itself.

**Drafts go to review; the system posts on approval.** Direct posting is
promotable later, per category, on evidence — the "send me the correlationId"
reply is the obvious first candidate once a run of them has been approved
unchanged.

**A draft records the message it was based on.** On approval, if newer messages
have arrived in that conversation, the reply is not posted: the task returns for
rework. This makes posting a stale answer impossible rather than unlikely, and
the check happens at the only moment that matters. A short debounce before
drafting keeps most follow-ups from creating a draft at all.

## Tools

A **shared registry**: every node may call any tool. Built-ins now; plugin
registration later.

Because scope does not constrain side effects, **the guard lives inside the
tool**. `post_reply()` checks task state and refuses unless the task is
`review`-approved, returning that refusal as a normal tool result rather than
raising — so the model can adapt ("that's blocked, I'll finish and let review
handle it"). The invariant holds no matter which node calls it.

**Tool failure:** retry transient failures inside the tool wrapper, then return
the error as a tool result so the model can adapt. A node that genuinely cannot
proceed burns turns and hits the cap, which already routes to `HITL`. Error
results must be truthful and specific — a tool that returns empty-on-failure
teaches the model there were no logs, and it will confidently conclude the wrong
thing.

## Memory

**Superseded 2026-09-06 (D9). What is below was built roughly as described —
staging plus an approval-gated promotion pass, `fact`/`person`/`lesson` in
place of the four kinds — and then removed, because it had stopped doing the
job it was built for. Read for the argument it made, not for what exists.**

Two tiers, because agents write memory *and* a compaction pass does.

- **Staging** — `remember(kind, text)` appends an observation scoped to the
  current task. The agent supplies only `kind` and `text`; the runner attaches
  `task_id` and `created_at`, because provenance the model writes is provenance
  the model can get wrong.
- **Long-term** — a capped, rewritten table, not a JSON file as first
  planned. A compaction pass reads staging plus completed tasks and promotes
  only what is corroborated by an outcome approved in `review`. Staging is
  cleared on promotion. Entries carry a support count; low-support entries
  expire.

The property being protected: the long-term set only contains things that
turned out to be true. A wrong entry there is invisible and self-reinforcing.

**Why it was removed anyway.** The protection held, and nothing ever tested
it: `remember` was cut from the tool list before any agent's job called for
noticing something worth keeping, so staging never received an entry and
promotion ran every heartbeat over an empty table — for months, silently,
which is exactly the failure mode this whole design exists to catch elsewhere.
A floor nothing ever stood on is not evidence the floor works.

**D9 — the decision.** An agent writes long-term memory directly, and reads
back what it wrote, because CRUD without retrieval is three tools nobody can
use: `memory_update` and `memory_delete` have nothing to name if the agent
never sees what it stored. The floor moves from "a human approved the task
this came from" to three narrower, structural guarantees instead of one
procedural one:

- **a memory reaches a model only as a tool result**, never appended to
  `instructions` — the class of failure a promoted note produced once
  (commit f0686f2: a note that closed its own section rewrote the instructions
  of every later call) is unreachable now by construction, because a tool
  result cannot do what a string concatenated onto `instructions` could;
- **scope is runtime-supplied**, carried on a context object and never named
  by the model, so a channel's memory cannot be read or written from another;
- **ids are opaque and sparse**, so a hallucinated one fails rather than
  landing on a neighbouring row.

Drift is possible under this design and is accepted: nothing corroborates a
memory before it is written. It is bounded by the channel scope, by the
operator being able to **see** what was written and by whom — including a
deleted line, and who deleted it — and by the fact that none of it reaches a
prompt except through a tool call the run chose to make — not by a count of
approved tasks agreeing. Removal is the agent's own, through `memory_delete`;
the board is read-only by design (`allow_methods=["GET"]`), so there is no
route for the operator to remove one directly. An earlier draft of this
sentence said "see and remove", which overstated the second half. See
`friday/tools/memory.py` for the shape and `CLAUDE.md` for what is wired.

## Orchestration

An asyncio worker with an explicit state machine, and a **configurable
concurrency pool** (semaphore) over tasks.

```
pending → processing → HITL → review → done
```

- **`HITL`** — blocked mid-run, needs your input to continue.
- **`review`** — finished, output waiting for approval before it posts.

Every outbound reply passes through `review`.

## Human loop

The **bot** DMs an approval card with buttons; the interaction resolves the
task. Buttons are an application-only Discord feature — a user account cannot
send message components — so approvals flow through the sanctioned bot API,
which also gives a clean audit trail of who clicked and when. The bot posts
nothing else and does not need to be in the watched channels.

**Everything the outside world sees comes from the user account.**

## Outbound

**A reply is a row, not a call.** Workflows return an outbound intent; the
Outbox delivers it. Nothing else calls a provider's `send()`.

This exists because deciding what to say and knowing where to put it are
different jobs, and because everything that can fail has to fail in one place.
Approval, audit, retry, rate limits and the manual-send list are then all views
over the same rows.

- **The row is written when the workflow decides**, not when approval arrives.
  The sender's query joins the task: `WHERE outbox.kind <> 'reply' OR
  tasks.approved_at IS NOT NULL`. Approval stays a fact about the task; the
  guard is one query rather than a check each caller must remember.
- **`kind` decides whether approval is needed** — see CONTEXT.md. Asking for a
  missing correlationId is the system completing a task's own required
  parameters, not the agent speaking for the operator, so it does not queue
  behind a human.
- **Delivery is at-least-once.** The row is marked sent after the API call, not
  before. A crash mid-send may post twice; the alternative loses an approved
  reply silently, and a lost reply is indistinguishable from working correctly.
- **Retries are bounded**, with backoff, both configured in `config.yaml`. On
  exhaustion the row goes `failed` and its task to `needs_human`.
- **A failed row is sent by hand.** The bot DMs it with its text; confirming
  moves the row to `sent_manually` and resolves the task. The distinction from
  `failed` is the audit trail: delivered by a human, not abandoned.

Its own delivery included — the approval card is an outbox row with
`kind = approval_card`. If it were sent directly, a failed approval DM would be
invisible and the task would wait forever for a decision nobody was asked for.

## Board — `:8086`

**Superseded 2026-09-06** by `.scratch/a-window-on-the-whole-path/`. What this
section described — FastAPI, server-rendered HTML, HTMX polling — was deleted
in that board's ticket 01, and what replaces it is a React SPA in `web/`
reading the JSON API in `friday/ops/api.py`. The original text follows, with
what changed marked.

> FastAPI, server-rendered HTML, HTMX polling. **Read-only** — it displays, and
> every action happens in Discord. That is what lets it run without auth.
>
> It is a **debug view**, not a control panel: tasks by state, the live message
> stream, model calls with their prompts and tool calls, outbound rows including
> what failed to send and its text to copy, plus per-provider connection status
> and the time of the last captured event.

**Still true:** it is a debug view; it answers on loopback only and is reached
over an SSH tunnel; every *decision* still happens in Discord — task state,
approvals and classifications are not editable from a browser.

**No longer true: "read-only".** One thing is writable, a channel's context
`overrides` — the section of `context/<channel>.yaml` the machine never touches
and which was created for the operator. That reverses this document's own
premise, and the argument is D7 on the new board: the rule exists so that
*decisions* have one home, and context is not a decision. It is also why
`check_exposure` stopped warning and started refusing (D10) — "unauthenticated
is safe because it is read-only" was an argument about writes, and there are
now writes.

**No longer true: "server-rendered HTML, HTMX".** The page is a static React
bundle built by a Node stage in the same Dockerfile and served by the same
FastAPI app. Still one process, one container.

## Ops

- One VPS, **one Docker container, one process**. Named volume for the DB.
- Single process follows from SQLite: multiple containers writing one file over
  a shared volume means writer contention and locking bugs.
- **Configuration in `config.yaml`** — pool size, per-node models and caps, the
  channel whitelist, the classifier threshold, memory caps. Version-controlled,
  so a model or cap change is a reviewable diff.
- **Secrets in `.env`**, `chmod 600`, strictly the three tokens. A log filter
  redacts token-shaped strings — the Discord user token is unscoped account
  access with no revocation short of a password change, and the realistic leak
  is an unhandled exception printing the gateway identify payload.
- **Liveness:** the bot DMs you if the user-gateway has been disconnected for
  more than N minutes, plus a daily "alive, processed N mentions" summary. The
  board also shows connection status. A dead container and a quiet day look
  identical without this.
- **A rejected credential alerts immediately**, not after the disconnection
  threshold. It is a known-terminal state, so waiting N minutes to report it
  only delays the one action that can fix it.

## Repo conventions

- Explicit `__init__.py` packages (not namespace packages).
- `pytest` + `pytest-asyncio` via `uv add --dev`.
- A node is testable through its declaration: pass fakes for the tools it uses
  and assert on its `output_schema`. Triage gets a fixture set of real messages
  with expected labels — the regression net for prompt changes.

## Open

**The `report_bug` and `tracing` procedures.** Each is now an ordered list of
nodes, and each node needs: prompt, tool list, `output_schema`, model, caps, and
whether it may pause. Also required: what systems the agent authenticates to,
what "done" means, and what gets posted back.

Until these exist the pipeline is a runner with two empty node lists, and four
things stay undecided: the nodes themselves, what credentials the agent needs,
whether a VPS can reach the relevant logs (this could force deployment back to a
local host), and the per-node cap values.

## Accepted risks

1. **User-account automation.** Automating a user account violates Discord's
   ToS and risks account termination; detection improved substantially after
   2024, and a long-lived authenticated gateway socket is the documented primary
   signal. It is non-optional in this design. The account is the single point of
   failure for the whole system. Decided deliberately after being raised.
2. **`discord-self` tracks a private API.** It can break on any Discord client
   change, with no SLA. Isolating the user-side in one module is the mitigation.
3. **Agents can write memory.** The staging tier and approval-gated
   promotion this line originally pointed at is gone (D9, see Memory above);
   the mitigation now is scope, visibility and the tool-result boundary
   instead of an approval gate. Not eliminated either way.
4. **Every node is an LLM call.** Cost and latency scale with mention volume,
   and a `skip` still costs a call. The threshold and node caps are the levers.
5. **The threshold and caps are unset by design.** The first weeks are data
   collection, not production. A busy `HITL` column early on is the system
   working.

## Build order

1. Ingestion + normalise + persist. Watch real mentions land in `events` before
   any LLM call exists.
2. The node runner + Triage as its first node, logging scores without acting.
3. State machine, board, approval DMs.
4. The two procedures, once specified.
5. Memory staging and the compaction pass, once there are completed tasks to
   compact.
