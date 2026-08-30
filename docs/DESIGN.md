# friday-agents — Design

Status: **settled, not yet implemented.** One branch remains open (see [Open](#open)).

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

## Ingestion

**Two identities, one process.** `discord.py` drives the bot; `discord-self`
(namespaced `discord_self`, so both import cleanly side by side) drives the user
account. Separate modules — the user-side is deliberately rip-out-able.

**Triggers:** direct `@you`, `@backend` role mention, and DMs.
**Scope:** an explicit channel whitelist, plus all DMs.

**Two delivery paths.** Discord has no inbound webhook for messages — delivery
is gateway-websocket-only — so redundancy is REST-based:

1. Gateway with RESUME (`session_id` and last `seq` persisted).
2. REST backfill sweep every ~5 min:
   `GET /channels/{id}/messages?after={last_seen_id}` per watched channel.

Both feed the same pipeline. **Dedup on `(provider, provider_message_id)`**
makes double-delivery a no-op. Path 2 exists because RESUME only replays if you
reconnect before the replay buffer overfills; past that you get Opcode 9 Invalid
Session and the gap is gone.

## Provider abstraction

An `InboundEvent` dataclass and a bidirectional `Provider` protocol with
`reply()` and `request_approval()`. The Discord implementation owns button
components and interaction payloads. Slack and Telegram are anticipated.

## Data model

SQLite, WAL mode, on a named Docker volume. Access is **async** (`aiosqlite` or
a thread executor) — a blocking DB call on the event loop stalls the Discord
gateways, which is the dropped-socket failure this design works hard to avoid.

| Table | Key | Holds |
| --- | --- | --- |
| `events` | unique `(provider, provider_message_id)` | every mention incl. skips: text, author, mention type, label, confidence |
| `sessions` | `(provider, channel_id, thread_id)` | conversation context |
| `tasks` | → session | work items and their state |
| `step_runs` | `(task_id, step_name)` | structured output + trimmed transcript per node |
| `memory_staging` | → task | agent-written entries awaiting promotion |

Gateway runtime state (`session_id`, `seq`, `last_seen_message_id` per channel)
lives in the same DB, so a container restart resumes instead of cold-starting.

## LLM runtime

**All OpenAI.** One provider, one key, one set of failure modes.

**A manual agent loop on the Responses API.** Per iteration: call
`client.responses.create(...)`, extend the input list with `response.output`,
append a `{"type": "function_call_output", "call_id": ..., "output": ...}` for
each `function_call`, repeat until no function calls remain. Reasoning items and
every other output item are replayed unchanged inside the loop.

`store=False` — conversation state is held client-side for the node's duration
and checkpointed at node end. Nothing conversational lives on OpenAI's servers,
consistent with SQLite being the single source of truth.

## Nodes

Every unit of reasoning is a **node**, declared as a frozen dataclass:

| Field | Purpose |
| --- | --- |
| `name` | stable identifier, and the `step_runs` key |
| `prompt` | instructions |
| `tools` | the subset taken from the registry |
| `output_schema` | JSON schema — becomes `text.format`, the next node's contract, and the test assertion |
| `model` | chosen per node |
| `max_turns` | tool-use iteration cap |
| `token_cap` | accumulated-usage backstop |
| `on_refusal` | behaviour when the model returns a `refusal` content part |
| `can_pause` | whether this node may return `Pause(question)` |

**Triage is a node** — `tools=[]`, `max_turns=1`, and the
`report_bug | tracing | skip` enum as its `output_schema`. One runner, one code
path; a misclassification is as debuggable as a bad investigation.

**A procedure is data, not a coroutine** — an ordered list of nodes. The runner
persists each node's result to `step_runs` before advancing, so resume means
loading the row and continuing at the first unfinished node. A node needing a
human returns `Pause(question)` rather than awaiting.

This matters because a suspended coroutine is process memory: a task sitting in
`HITL` across a container restart would know its state but not its position, and
could only re-run from the top, repeating side effects.

**Checkpoint contents:** the structured output (the contract — downstream nodes
may read only this) plus a trimmed transcript of tool calls and results
(diagnostics — nothing downstream may read it). Trimming happens at node end,
never inside the loop.

**Abnormal exits — all route to `HITL`, never to a silent discard:**

- turn cap exceeded (~8–10 for investigation nodes),
- token cap exceeded (accumulated from per-iteration `usage`),
- **refusal** — arrives as a completed response whose message content part is
  `type: "refusal"`, not as an error, so the loop must check for it explicitly.

There is no model-visible budget primitive here, so a capped node gets cut off
rather than wrapping up gracefully. That is an argument for keeping nodes small.

**Prompt assembly is stable → volatile:** system instructions, the long-term
memory file, and tool definitions first; then prior node outputs; then the
triggering message and thread context last. Caching is prefix-match, so anything
after the volatile section is uncacheable. Per-node models mean per-model
caches — nodes on different models share nothing.

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

Two tiers, because agents write memory *and* a compaction pass does.

- **Staging** — `remember(kind, text)` appends an observation scoped to the
  current task. The agent supplies only `kind` and `text`; the runner attaches
  `task_id` and `created_at`, because provenance the model writes is provenance
  the model can get wrong.
- **Long-term** — a capped JSON file. A compaction pass reads staging plus
  completed tasks and promotes only what is corroborated by an outcome approved
  in `review`. Staging is cleared on promotion. Entries carry `support_count`;
  low-support entries expire.

`kind` is a closed enum — `recurring_problem`, `tone_preference`, `system_fact`,
`person_fact` — so promotion can apply different rules per kind (a
`tone_preference` needs no corroboration; a `recurring_problem` should need
several approved tasks).

The long-term file is **capped and rewritten, not appended**: it sits in every
prompt's stable prefix, so appending per task would invalidate the cache every
task and eventually eat the context window. Rewrite on promotion, not per task.

The property being protected: the long-term file only contains things that
turned out to be true. A wrong entry there is invisible and self-reinforcing.

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

## Board — `:8086`

FastAPI, server-rendered HTML, HTMX polling. Read-only: five state columns, plus
per-provider connection status and last-event timestamp. All interaction happens
in Discord.

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

## Classification policy

Structured output against a closed enum with a confidence score, over the
message *and* its thread context — without context, follow-ups ("still broken
btw") misclassify.

- `skip` → event row, no task. Retained to surface false negatives and to build
  the labelled set the threshold is derived from.
- A follow-up in a session with an open task **updates that task**; if the label
  changes, it routes to `HITL`.
- Below threshold → `HITL`.

Start the threshold high and log every scored decision; set the real number from
the observed distribution, not from a guess.

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
3. **Agents can write memory.** Mitigated by the staging tier and
   approval-gated promotion, not eliminated.
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
