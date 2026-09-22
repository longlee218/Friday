# CONTEXT

Three things, kept apart: **the rules** that must not be lost, **where the
project stands** (strategy, boards, roadmap), and **the vocabulary**. Dated
progress — tickets, measurements, evals, milestones, open asks — is not here:
it is one JSON object per line in **`.scratch/progress.jsonl`** (§ Tracking).
Architecture is `docs/DESIGN.md`; how to work is `CLAUDE.md`.

Read § Rules first. They are at the top, and repeated in one line each at the
bottom, because a rule found in the middle of a long file is a rule that gets
missed.

---

# Rules that must not be lost

Each is a standing decision by the operator or a measured fact; the pointer
says where it is argued. Breaking one is a design change, not an
implementation detail.

**What Friday is allowed to do**

1. **Friday never writes code and never executes it.** Every tool is a read;
   the only outputs are a report and outbox rows a person approved.
   (`docs/DESIGN.md` § Running it; spec D6.)
2. **Nothing is sent by the caller that decided to send it.** An outbound
   message is a row; one loop delivers it; `Channel.send`/`provider.send` has
   one caller. **Approval belongs to the row**, not the task. Only `reply`
   waits for approval; the acknowledgement, `ask_for_details`, the approval
   card and operator-facing kinds do not. (§ Approval.)
3. **A model never chooses the next step of a workflow.** The shape is code.
   Inside one node an agent may choose which *read* to make next (v3.3,
   `Diagnose` with tools) — never which node runs.
4. **Never drop a mention.** Low confidence, refusals, errors, caps, the
   prefilter — all route to a person. An old turn is recorded `outdated`,
   not discarded. The prefilter **holds**, it does not skip.
5. **The model decides what to look for; code decides where and what comes
   back.** Every "where" a tool accepts is a closed enum built from knowledge
   rows; tool output is narrowed and distilled in code (171 KB raw window →
   8 lines, measured 2026-09-22).

**How evidence and memory are treated**

6. **Verbatim material is stored whole, as an `Artifact`, and pointed at —
   never retyped.** Models *point* at lines by id and code fills the text
   (pointing 20/20, quoting 32/40, measured 2026-09-18). A pointer that
   resolves to nothing voids the answer (the grounding gate).
7. **Every fact about where a request went is a row, not a rule in code** —
   `environment`, `route`, `service`, `project`, `dependency`. A missing row
   is a hand-over, not a guess.
8. **Memory is one table, thirteen kinds, origin `model` | `admin`.** A model
   may not alter an admin row; instruction-shaped text is refused at the one
   write path; **silence is not approval** — candidates wait for the
   operator's mark. (§ Memory.)
9. **The reporter's credential never survives:** scrubbed where an artifact
   is written and where the extractor reads; the Discord token never reaches
   logs, tracebacks or the database.
10. **Production data stays on this machine.** Captured cases live in
    `data/cases/` (gitignored) because their lines carry `userId`, `ip`,
    `deviceId`; a case travels only if somebody deliberately sends it.

**How the code is built**

11. **One seam per outside library.** Only `friday/agent/harness.py` imports
    the agent SDK; a second module reaching for it is the thing to push back
    on. The same rule will hold for every library adopted later.
12. **Reuse before rewrite** (operator, 2026-09-22): a maintained library that
    covers the need is adopted rather than rebuilt; Friday writes its
    invariants, its domain and the glue. Its cost is written next to the
    decision. (Roadmap item 5.)
13. **A rule worth stating is worth a test**; a guard is deleted once and
    watched go red. **Measure before building**: a number in `config.yaml` or
    a spec needs a "measured on" date.
14. **Triage classifies and nothing else**, into one closed set; extraction
    lifts values, one extractor per type. Two producers on one field is a
    coin toss with a rationale.
15. **Only the responder carries the operator's voice.** Triage and the
    extractors carry none.
16. **ORM only** — SQLAlchemy 2.0 async and Alembic; never hand-written SQL
    or `ALTER`. Migrations autogenerate against a throwaway database.
17. **Design questions to the operator in Vietnamese; code and docs in
    English.**

---

# Project state

## Running

Friday ingests Discord mentions, classifies them, opens tasks, asks for
missing details, investigates `api_issue` (the six-node slice `Prepare →
Resolve → FindRequestLog → ReadFailingCode → Diagnose → Report`, plus an
unapproved acknowledgement), and sends approved replies as the watched
account. It runs on the operator's own machine, on `main`, with a passing
suite. Triage scored 100% on `evals/triage.jsonl` on 2026-09-20.

## Technology

| Layer | What | Notes |
| --- | --- | --- |
| Language, packaging | Python 3.13, **uv** | `uv add` only; never hand-edit `pyproject.toml` |
| Storage | **SQLite** (WAL), **SQLAlchemy 2.0 async**, **Alembic** | the only state store; one process |
| Models | **Pydantic AI** (`pydantic-ai-slim`) over **Chat Completions**; **MiniMax-M3** at `api.minimax.io` | provider is `base_url`/`api_key`/`model` (or a `provider:` shorthand); one module imports the vendor (`friday/agent/harness.py`) |
| Workflows | **DBOS** (`dbos` 3.0, in-process) durable workflows on their **own SQLite system DB** beside the app db | the hand-written DAG engine is retired; the port is `friday/sdk/workflow.py`, the adapter `friday/workflow/adapter.py` — the one module that imports `dbos` |
| Chat | **discord.py** (bot: buttons, DMs), **discord-self** (the operator's account: reads and replies) | the self-bot is an accepted risk |
| Tool servers | **MCP** over streamable HTTP: `devops-generic` (Loki, k8s reads), `db-generic` | the operator's SSO session, refreshed by the process (`authorize.py` once) |
| Dev logs | `kubectl` on the dev host via `ssh dev` | no kubeconfig on this machine |
| Code reading | the operator's clones under `~/Documents/Apero/`, CodeGraph | read-only |
| Board | **React + Vite** SPA served by FastAPI on `:8086`, SSE live feed | loopback only |
| Tests, eval | **pytest** + pytest-asyncio; `evals/run_triage_eval.py` by hand | eval is not in the suite |

## Boards — what each spec set out to do

Status per ticket is in `.scratch/progress.jsonl` (`"kind": "ticket"`); this
is the premise each board tracks against.

- **`discord-mention-triage`** (spec `docs/SPEC.md`) — the base service:
  capture mentions, triage, turn real ones into tasks, approve by button
  before anything posts, show it on a board. Later reshaped into one harness,
  outbound rows, DAG workflows, prompts per agent family. **Done**
  (38 done, 4 withdrawn).
- **`every-task-is-a-graph`** — one engine: every task type is a graph (a
  trivial type is one node), node 0 `prepare` extracts and validates, the pool
  owns the task lifecycle only. **Done.**
- **`skill-tools`** — `search_skills`, `describe_skill`, `read_skill_file`
  beside `fetch_skill`; `Skill` gains `mutability` and `allowed_tools`.
  `issues/04-review-fixes.md` overrides five of the spec's decisions. **Done.**
- **`nothing-runs-unmeasured`** — one middleware around every model call:
  clock, budget before the call, retries, a recording sink; per-agent token
  caps; a frozen triage eval run by hand. **Done.**
- **`a-window-on-the-whole-path`** — the old board deleted, a React SPA on
  the same API: flow, task and context screens. **Done.**
- **`a-monitor-on-the-whole-path`** — the SPA became a real-time Monitor at
  `/`: tokens not literals, SSE, axe and bundle-size gates. **Done.**
- **`what-the-room-already-knows`** — context built twice (light for triage,
  full at node 0), compaction by budget, verbatim material as artifacts,
  domain memory with candidates and instruction guard; law: every store ships
  with its producer and consumer. **Done.**
- **`work-that-has-gone-cold`** — a message older than `max_message_age` is
  recorded `outdated` at triage; a cold cursor looks back only that far.
  **Done**, one open question on ticket 02.
- **`every-answer-has-a-shape`** — every model answer is a typed dataclass
  through a generated tool, checked in-process, one correction (MiniMax
  ignores `response_format`); `FridayState` is the one run state; triage is
  one closed set with `skip`. **In progress:** ticket 03 (the eval set and
  its baseline) is the operator's to label.
- **`read-it-the-way-the-operator-does`** — `api_issue` rebuilt from the
  operator's own routine: environment from rows, production logs through the
  devops MCP, dev logs over `ssh dev`, code at the running release, a
  diagnosis that says what it did not check. Memory became one store. **In
  progress.** **What stands now is v3.3 (operator, 2026-09-22): `Gather`
  gathers metadata only; `Diagnose` reads for itself through tools that
  distil; the Check layer and the Collector are withdrawn; ticket 14's
  labelled eval is a precondition and the two old nodes go only if the
  measurement says the new way is not worse.** Revision v4 in the same spec
  is *proposed* additions to v3.3, not a competing design (see the open item
  in the tracking file).

## Roadmap — decided in direction, not yet boards (2026-09-22)

1. **Library-independent defects, first** (DESIGN-v2 §15 step 1). Landed:
   board protection (ticket 02) — `BOARD_TOKEN` removed and every write checks
   Host/Origin/CSRF (`friday/ops/api.py`); approver identity (ticket 03) — a
   decision is checked against `operator_id` in `record_decision`
   (`friday/outbox/__init__.py`), not trusted from whatever button was pressed.
   Still to do: a single-instance lock on `run_agent.py` (ticket 04). **The
   outbox double-post fix** folded into the DBOS phase as planned (§15 step 3,
   ADR 0001) and **landed as ticket 07**: each delivery is a DBOS workflow,
   `dispatching` is written before the channel call, an interrupted send on a
   channel that cannot dedupe becomes `delivery_unknown` for the operator, and
   `approved_payload_hash` voids an approval whose message changed.
2. **DESIGN-v2** (`docs/DESIGN-v2.md`, **accepted target 2026-09-22, ADR
   0001**): a kernel owns the invariants, everything else registers as an
   in-repo plugin. **Re-sequenced §15**: library-independent defects (step 1)
   → the runtime libraries as the foundation, Pydantic AI (step 2) then DBOS
   (step 3) → the registry / `sdk`–`kernel`–`plugins` split → adapters on
   trigger. `docs/DESIGN.md` stays the as-built record; sections move across
   as each step lands.
3. **Triage at scale** — *one task type = one distinct graph* (two types
   sharing a graph are one type with a parameter); *retrieve, then classify*
   once the enabled set grows.
4. **`api_issue`: finish v3.3 and fold in v4's proposals** — the Prepare/Map
   split by source (the domain beats the reporter's words; `environment`
   becomes a hint), a code supervisor for grounding and coverage, layered
   ceilings on the tool loop with nothing lost when one is hit, and raw-window
   capture so replay answers any query. Placed after DESIGN-v2 by the
   operator; v3.3's own order starts earlier — see the open item.
5. **Runtime libraries** (*reuse before rewrite*) — **the foundation, before
   the plugin migration, not after it** (ADR 0001, reversing the earlier
   order): **Pydantic AI** replaces openai-agents (spike 15/15 on MiniMax-M3
   and the real MCP server, `docs/research/pydantic-ai-migration.md`) as §15
   step 2 (done, tickets 05/18), drawing the `ModelProvider`/harness seam;
   **DBOS** (in-process, SQLite) has replaced the hand-written DAG engine as
   §15 step 3 (done, ticket 06) behind the thin `sdk/workflow.py` port — a
   graph is a `@DBOS.workflow`, `Ask` suspends the run and the reporter's
   answer re-runs the asking node; the pool drives durable workflows and the
   spike precondition is cleared; **Jev** as a model
   via `TypeSafeModel` + `FallbackModel`, shadow-run first
   (`docs/research/jev-decision-models.md`). Not adopted: Harness `Skills`,
   `DynamicWorkflow`. Temporal only on more than one machine. Open: may a
   model *plan* a workflow that code validates?

## Tracking

`.scratch/progress.jsonl`, one object per line, one `kind` per row:

| kind | keys | what |
| --- | --- | --- |
| `board` | `board, spec, status, dates, summary` | one per board |
| `ticket` | `board, ticket, title, status, label, status_line, updated, path` | status is `done`, `part-done`, `not-started`, `blocked`, `withdrawn` or `proposed`, read from the ticket's own `**Status:**` line |
| `measurement`, `eval`, `milestone` | `board, date, what, source` | dated facts; the number and the date it was measured |
| `open` | `board, date, what, source` | waiting on the operator or unreconciled |
| `note` | `board, date, what, source` | a known gap worth saying out loud |

`uv run track_progress.py` rebuilds the `board` and `ticket` rows from
`.scratch/` (hand-written rows are kept) and writes `data/progress.html`;
`sync --dry-run` shows what would change. Append the other kinds by hand when
a number is measured or something waits on the operator; the ticket file
stays the source of truth and the row points at it. Read it with
`jq`, e.g. `jq -c 'select(.kind=="open")' .scratch/progress.jsonl`.

---

# Vocabulary

Use these words in code, tests, tickets and commit messages; where a word had
two meanings, this picks one.

## Message

An inbound message from a chat platform, normalised to one shape whatever the
platform. **A message is not a task**: most are context, some address the
operator, few become work. One that addresses the operator carries a
**mention type** — `direct`, `role` or `dm`; one without was seen but not
addressed and is kept, because a conversation missing half of itself does not
read. One table holds both.

## Turn

A person's consecutive messages read as one unit — the unit triage
classifies. Computed when read, never stored.

## Transform and prefilter

**Transform** turns a platform message into something readable: prose
cleaned, code left exactly as it was, attachments named. **Split before
cleaning** — code comes out first, is never touched, and goes back where it
was, because a `curl` or a stack trace is the part a value is lifted out of.

The **prefilter** is not cleaning: a word list the operator maintains keeps
some messages (pay, medical, passwords, key requests) from reaching a
third-party model at all, decided before the call. It **holds**, it does not
skip: the guarantee is that the model does not see it, not that nobody does.

## Conversation

Where an exchange happens: a channel, a thread or a DM, identified by
`(provider, channel_id, thread_id)` — a thread and its parent are different
conversations. **Not "session"**: that word belongs to the agent SDK's
transcript and to Discord's gateway.

## Task

A piece of work derived from a message: a type, a confidence, and parameters.
A conversation has at most one open task; later messages are follow-ups.
**Parameters matter more than the type** — the commonest real action is
noticing an `api_issue` arrived without what makes it findable.

## Triage

Deciding what a message is — **that, and nothing else**. Produces a decision
(a type from one **closed set** — every task type plus `skip` — and a
confidence) and writes nothing. Every message ends `Decided` or `NeedsHuman`;
there is no silent discard. A `NeedsHuman` says which failure it was — a
label outside the set is a prompt or model fault, no answer is a network
fault — and the eval reports them apart. Triage extracts no parameters: a
tool schema with `correlation_id` in it *is* extraction, whatever the prompt
says.

## Extraction

Lifting the values a task needs out of what the reporter wrote: one extractor
per task type, owning its prompt, schema and model. It reads **every**
message linked to the task, oldest first, inside node 0 (`prepare`). **The
first answer for a field stands**: a later run may fill a blank, not revise a
value. The result is one validated object — the type's parameters plus
`ask_about` (which of its own fields to ask the reporter about) and
`because`. Code stays the floor: a value the type's rules reject is
challenged regardless.

## Extraction mark

What node 0's last extraction was made from and what it came to — one row
per task, rewritten when the reporter says something new. Fingerprinted over
**the reporter's text and the field schema only**, so no model is called when
nothing arrived. Records the outcome (values and the question asked), so a
skipped call behaves like the call. Distinct from a graph's checkpoint: a
checkpoint holds what nodes *returned* and is discarded when parameters
change; a mark holds what node 0 was *given* and outlives that.

## Pool

The loop that hosts tasks' graphs — deciding only *when*, and *whether what
came back may be sent*, never *what* to do. Every pass: stand down for a task
the operator answered, announce to the operator what nobody can act on, host
graphs for pending tasks (bounded concurrency), and turn what came back into
rows. Lives in `friday/tasks/`, apart from the engine.

## Action

What a graph returns about a task — `Ask`, `Reply` or `HandOver` — never a
side effect. Every task first **fills in** what the message carries and
**checks** it against the type's rules; then its graph decides. `Ask` goes to
the reporter as a question; `Reply` answers the reporter in the operator's
name and **waits for approval**; `HandOver` goes to the operator only and
never reaches the reporter. A task missing something it cannot work without
has to say so: required-ness is read off the parameter type, except where a
type overrides it (`api_issue`: a correlationId *or* a curl makes a request
findable). **A precondition belongs in the gate, not in the last node's else
branch.** `api_issue`'s `Report` produces a `Reply` (a brief) today.

## Graph

How a workflow of more than one decision is made. A **node** is
`async (state, deps) -> result`; an **edge** may carry a predicate, and the
first that holds is taken. Deterministic Python.

- **Node 0, `prepare`**, extracts and validates and runs fresh on every pass;
  it is never checkpointed. Its output is what state is discarded against.
- A graph **checkpoints after every other node**, keyed on its **version** — a
  digest of node names and edges — so a changed shape never inherits old
  results.
- **One invoke** (`DAGRunner._invoke`): the node's timeout, retries over an
  explicit exception list with doubling backoff, any other exception turned
  into a result. A result is an `Action` (ends the run) or an **envelope** —
  `status` (`ok`, `empty`, `skipped`, `timed_out`, `error`) and `reason`. A
  node that cannot do its job **skips out loud**. Each attempt is a **node
  run** row.
- A node that calls a model names its `agent`; its clock must outlast the
  agent's by a margin, checked at load — two equal clocks race.
- An agent is a node inside a graph, never the thing driving it; which agent
  and server a node gets is composition, handed in through `deps`.

## Source, check, node

Three layers. A **source** (`friday/sources/`) reads one kind of thing and
decides nothing — the only package that reaches an outside read surface. A
**check** is a formula over sources (being withdrawn by v3.3 in favour of
distilling tools). A **node** is the frame a run is checkpointed, timed and
retried in. Reuse lives in the first layer, not the third.

## Tool server

Tools outside this process, over MCP. A server is **configuration** (a block
in `config.yaml`); which of its tools may be called is **declared in code**
on the class that calls them and enforced twice — a filter at build time and
a check on every call. Config may not widen it.

## Harness

The one place an agent is *run*: client and `base_url`/`api_key`/`model`,
model settings, recording hooks, turn and token caps, the clock, retries, and
the rule that any failure becomes work for a person rather than silence. An
**agent declaration** is only what differs: instructions, tools, output
shape. Structured answers come back through a generated **answer tool**,
checked in-process, with one correction turn.

## Voice

How an agent is told to write, as distinct from what to do — part of that
agent's own prompt module. Only the **responder** carries the operator's
voice; triage and the extractors carry none, because a word spent there is
paid on the highest-volume calls for nothing. Where the written voice and the
operator's real messages disagree, the messages win.

## Flow

Everything that followed from one message — the turn, triage's decision, the
task, every model and tool call, the outbound rows. A read-side assembly
(`Database.flow_for` → `MessageFlow`), never stored. **Its spine is a
message**, because a task-spined flow loses triage and every `skip`. Not a
`Graph`: a flow contains one as a step.

## Channel summary

A room's transcript reduced to four fields — `topic`, `facts`, `decisions`,
`constraints` — stored as the room's one active `summary` memory row,
superseded on rebuild. Rebuilt whenever the room has said anything since the
last one. **Capped, and a cap refuses rather than trims**: the stored summary
stands when a fresh one is refused. Bookkeeping sits in `data` and is never
rendered. Read by triage and the responder only.

## Friday state

What one message's journey knows about itself — the room, the agent, and as
supplied the provider, thread, message, author, reply and task. The SDK's
per-run `context` carries it and nothing else. **Read-only**: every change is
a named method returning a new state (`as_agent`, `for_task`,
`about_message`); there is no setter. Room and agent are required — they are
the boundary and the provenance.

## Memory

A row in `memories`, in one of **thirteen kinds**: `fact`, `constraint`,
`decision`, `finding`, `voice`, `runbook`, `summary`, `project`, `service`,
`route`, `dependency`, `person`, `environment`. Who reads a row follows from
its kind (`readers_for`); who may write it is enforced at
`Database.memory_add`. A model sees and writes five kinds
(`ModelMemoryKind`) through `memory_search`, `memory_add`, `memory_propose`,
`memory_update`, `memory_delete`. A row has an **origin** — `model` or
`admin` (the operator, on the board) — and a model may not update, supersede
or delete an admin row. Structured kinds carry `data` checked against the
kind's schema and a natural **key**, one active row per room — except
`finding`, keyed `service:error_code`, which piles up.

**Scope is runtime-supplied** on `FridayState`, never named by the model: a
room's memory is invisible to another room; `*` is every room. Ids are
opaque. Memory reaches a model as a **tool result**, or injected into the
extractor's input labelled by who answers for it — never through an agent's
instructions. Instruction-shaped text ("send without approval") is refused at
the one write path. A proposed memory waits in `memory_candidates` for the
same mark that confirms a classification.

## Artifact

Verbatim material — a curl, a stack trace, code, SQL — stored whole and
referred to by id (`[artifact id: description]`), never paraphrased or
retyped. Code copies it; a model only names it.

## Outbound intent

Something to send, held as a row rather than performed as a call: the
conversation, the text, the **sender**, what it replies to, and its **kind**.
Kinds: `acknowledged`, `ask_for_details`, `reply`, `approval_card`,
`help_wanted`, `alert`, `summary`. **Only `reply` needs approval** — it is the
agent speaking as the operator about a cause.

## Outbound state

`queued`, `dispatching` (the channel call is in flight — written before the
send), `sent`, `delivery_unknown` (interrupted mid-send on a channel that
cannot dedupe: may have gone out, so it waits for the operator, never
auto-retried), `failed`, `sent_manually` (a person sent it — not abandoned),
`cancelled` (withdrawn because the operator answered first). One definition,
in `friday/domain/states.py`.

## Outbox

The only module that delivers: dispatches each row to the adapter its
`sender` names, retries within a bound, gives up to a person, and checks at
the last moment that an answer has not gone stale. **Each delivery is a DBOS
workflow** (ticket 07), so a crash mid-send resumes exactly once —
`Outbox.deliver_once` is the durable unit, run through
`adapter.deliver_outbound`.

## Approval

A fact about **one outbox row**: who approved that reply and when, and the
message that was approved, frozen as `approved_payload_hash`. A row whose kind
needs approval is sendable only while it carries one; the approval card names
the row it approves (`approves`), so answering it releases that reply and
nothing queued after it. A kind that needs no approval is **policy-approved**
at enqueue (`approved_by = policy`), hashed there too. The hash is recomputed
at dispatch: a text edited after approval no longer matches, so the approval is
void. Reading logs and code needs no approval — the risk is in speaking, and
Friday takes no other action.

## Idempotency key

What a channel dedupes a repeated send on (`outbox-{row}`, stable across
retries). A sender declares support with `supports_idempotency = True`; the
default is `False` — the safe assumption for a channel not proven to dedupe,
which is why an interrupted send on one lands in `delivery_unknown` rather than
being re-tried.

## Hand-over

The `Action` for "cannot conclude". Its reason is the system's own finding,
quoted to the operator; the task moves to `needs_human` and the pool
announces it once. **Not** "handled by the operator", which is a person
having acted.

## Handled by the operator

A task the operator answered themselves — not `done`, and reopenable.
Everything queued about it is withdrawn silently. A reply closes the task it
answers; a message replying to nothing closes the conversation's task only
when there is exactly one. The share of tasks ending here says whether Friday
is helping.

## Sender

Which identity speaks: `discord_user` (the operator's account — everything
the reporter sees) or `discord_bot` (buttons and DMs to the operator, which a
user account cannot send).

## Provider

A chat platform as the rest of the system sees it: normalised messages in,
outbound rows out; platform mechanics stay inside.

## Sweep

The recovery path: channel history re-read from a stored **cursor** on a
timer and on reconnect. A channel with no cursor is a **cold cursor**, read
back only as far as the **lookback** — `max_message_age`, the same number that
marks a turn `outdated`.

---

# The rules again, one line each

1. Never write or run code; every tool is a read.
2. Nothing is sent except by the outbox; only `reply` waits, and approval is per row.
3. A model never chooses the next workflow step.
4. Never drop a mention; `outdated` is recorded, the prefilter holds.
5. The model chooses what to read; code chooses where and what comes back.
6. Verbatim material is an artifact, pointed at, never retyped.
7. Where a request went is a knowledge row; a missing row is a hand-over.
8. Thirteen kinds, one table; admin rows are the operator's; silence is not approval.
9. Credentials are scrubbed before they are stored; tokens never reach logs.
10. Captured production cases stay in `data/cases/`.
11. One module imports each outside library.
12. Reuse a maintained library before rewriting it.
13. A stated rule has a test; a number has a measured-on date.
14. Triage classifies; extraction extracts.
15. Only the responder has the operator's voice.
16. ORM and Alembic only.
17. Ask the operator in Vietnamese; write code and docs in English.
