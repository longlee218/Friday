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

11. **One seam per outside library.** Only `friday/kernel/harness/harness.py` imports
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
missing details, investigates `backend.trace_problem` on the spine (a durable
pass: Intake → an unapproved acknowledgement → Planner + GatePlan → the
`backend.diagnose` agent → a drafted reply; build-the-spine ticket 14), and
sends approved replies as the watched account. It runs on the operator's own machine, on `main`, with a passing
suite. Triage scored 100% on the eval set (then `evals/triage.jsonl`) on 2026-09-20.

## Technology

| Layer | What | Notes |
| --- | --- | --- |
| Language, packaging | Python 3.13, **uv** | `uv add` only; never hand-edit `pyproject.toml` |
| Storage | **SQLite** (WAL), **SQLAlchemy 2.0 async**, **Alembic** | the only state store; one process |
| Models | **Pydantic AI** (`pydantic-ai-slim`) over **Chat Completions**; **MiniMax-M3** at `api.minimax.io` | provider is `base_url`/`api_key`/`model` (or a `provider:` shorthand); one module imports the vendor (`friday/kernel/harness/harness.py`) |
| Workflows | **DBOS** (`dbos` 3.0, in-process) durable workflows on their **own SQLite system DB** beside the app db | the hand-written DAG engine is retired; the port is `friday/sdk/workflow.py`, the adapter `friday/kernel/dag/adapter.py` — the one module that imports `dbos` |
| Chat | **discord.py** (bot: buttons, DMs), **discord-self** (the operator's account: reads and replies) | the self-bot is an accepted risk |
| Tool servers | **MCP** over streamable HTTP: `devops-generic` (Loki, k8s reads), `db-generic` | the operator's SSO session, refreshed by the process (`authorize.py` once) |
| Dev logs | `kubectl` on the dev host via `ssh dev` | no kubeconfig on this machine |
| Code reading | the operator's clones under `~/Documents/Apero/`, CodeGraph | read-only |
| Board | **React + Vite** SPA served by FastAPI on `:8086`, SSE live feed | loopback only |
| Tests, eval | **pytest** + pytest-asyncio; **Pydantic Evals** via `uv run run_eval.py <eval>` by hand | eval is not in the suite; one module imports `pydantic_evals` (`friday/kernel/evals/run.py`) |

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
- **`work-that-has-gone-cold`** — a message older than `MAX_MESSAGE_AGE_SECONDS` is
  recorded `outdated` at triage; a cold cursor looks back only that far.
  **Done**, one open question on ticket 02.
- **`every-answer-has-a-shape`** — every model answer is a typed dataclass
  through a generated tool, checked in-process, one correction (MiniMax
  ignores `response_format`); `FridayState` is the one run state; triage is
  one closed set with `skip`. **In progress:** ticket 03 (the eval set and
  its baseline) is the operator's to label.
- **`read-it-the-way-the-operator-does`** — `trace_problem` rebuilt from the
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
- **`build-the-spine`** — builds the `domains-plug-in` map: a plugin is a
  domain registering actions (intent + contract), agents and toolsets; every
  task runs on one durable spine (Intake → acknowledge → Planner + GatePlan →
  run → deliver). 21 tickets; rename first, DAG path deleted in ticket 16;
  ticket 20 (opened 2026-09-30, after 14) keeps the Planner to goals — a brief
  is not a method, the grant is the agent's (ticket 22: the Planner writes
  no `toolsets`), `replan` says when;
  ticket 21 (after 20) puts the Planner's prompt through the one assembler
  and the trust boundary, which it alone skips today; ticket 22 (done
  2026-09-30) takes the grant from the Planner and splits `core.memory`
  (reads + propose) from `core.memory_write`, so `backend.diagnose` cannot
  write memory.
  **In progress:** tickets 01–03 and 05–13 done (2026-09-29; names are
  `backend.*`/`ops.*`, live db wiped; the backend's tools are its four
  toolsets, reading code at the running tag; triage's prompt is assembled
  from the three registered actions' recognition; the Planner runs on a
  `strong` tier). Ticket 14's code is in (2026-09-30): `backend.trace_problem`
  runs only on the spine, the other two actions on their DAG's node 0 until
  15–16; its paid eval run and one real end-to-end run wait on the operator.
  04 is takeable
  (`.scratch/build-the-spine/STATUS.md`). Evals run on Pydantic Evals
  (`uv run run_eval.py <name>`); `core.triage` measured 2026-09-29 (deepseek
  34/35; triage runs on qwen3-30b, 21/23 on `trace_problem`); `core.planner`
  6/8 on glm-5.3-flash (2026-09-29), 7/8 and 8/8 after ticket 22
  (2026-09-30, grant no longer graded). The
  `backend.trace_problem` eval has not been run on the new key yet.

## Roadmap — decided in direction, not yet boards (2026-09-22)

1. **Library-independent defects, first** (DESIGN-v2 §15 step 1). Landed:
   board protection (ticket 02) — `BOARD_TOKEN` removed and every write checks
   Host/Origin/CSRF (`friday/kernel/ops/api.py`); approver identity (ticket 03) — a
   decision is checked against `operator_id` in `record_decision`
   (`friday/kernel/outbox.py`), not trusted from whatever button was pressed.
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
4. **`trace_problem`: finish v3.3 and fold in v4's proposals** — the Prepare/Map
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

**Agent turn** is the other sense, always qualified: one request to the model
in one agent run whose answer (or tool call) the next request builds on —
tool turns included. `max_turns` counts agent turns; it is the only turn
budget (board `domains-plug-in`, ticket 17). Not an **attempt**.

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
noticing a `trace_problem` arrived without what makes it findable.

## Domain

A plugin, as the catalog sees it: `backend`, `ops`. Every task type is named
`<domain>.<name>` (`backend.trace_problem`, `backend.answer_question`,
`ops.request_permission`); the kernel owns none besides `skip`, which is not a
task type. The board colours a task's tag by its domain, read from
`/api/actions` (`[{name, domain}]`). (Build-the-spine ticket 02.)

A domain may carry one **enricher**: the function (`Plugin.enricher`) that
turns a task's intake seed into the domain's own value (backend's
`Placement`), from the DB only, no network. Its return annotation is the
**domain type** — a toolset that reads `run.domain` declares the same type,
and the boot refuses a contract granting one of another type. No enricher
(`ops`) → domain `None`. (Build-the-spine ticket 05.)

## Triage

Deciding what a message is — **that, and nothing else**. Produces a decision
(a **label** from one **closed set** — every registered action plus `skip` —
and a confidence) and writes nothing. Every message ends `Decided` or `NeedsHuman`;
there is no silent discard. A `NeedsHuman` says which failure it was — a
label outside the set is a prompt or model fault, no answer is a network
fault — and the eval reports them apart. Triage extracts no parameters: a
tool schema with `correlation_id` in it *is* extraction, whatever the prompt
says.

**Label** — what triage answers: an action's name, or `skip` (the core's, owned
by no plugin). Its meaning is the action's recognition, rendered under
`<labels>` in the **assembled triage prompt** — the core's reasoning + every
action's recognition sorted by name, `skip` last + examples that add up
(declared → `skip` → operator-confirmed). No label comes first.

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

The loop that claims pending tasks and starts their passes — deciding only
*when*, never *what*. Every pool pass: stand down for a task the operator
answered (and cancel its running pass), start or join each pending task's
spine pass (bounded concurrency), and announce to the operator what nobody
can act on. Routing is the pass's own `deliver`. An action not yet on the
spine runs its DAG's node 0 here and goes through the same `deliver` (until
build-the-spine ticket 16). Lives in `friday/kernel/pool/`.

## Eval

A named set of **cases** and how each result is judged, run against the live
provider by hand (`uv run run_eval.py <name>`), never by the suite. Declared
as an **eval spec** (`EvalSpec`: cases, per-case checks, a report) and
registered with `api.eval` under the owner's namespace (`core.triage`,
`core.planner`, `backend.trace_problem`); the core runs it on Pydantic Evals. A **case** is what
one run is given and what a right answer is — for triage, a markdown file
under `evals/datasets/triage/<label>/`: one message, or a turn of messages
(long ones kept in `_messages/`), whose folder is its expected label.

## Action

One kind of work a domain offers, registered by its plugin
(`api.action(Action(...))`, `friday/sdk/action.py`), named
`<domain>.<name>`: its **recognition** (what triage reads — `means`,
`pick_when`, `not_when`, examples) and its **action contract**. Replaces the
task type (`TaskTypeSpec`) at build-the-spine ticket 16.

**Action contract** — the ceiling the plugin author writes in code for one
action: allowed step types, agents and toolsets, constraints, approval
policy, acceptance template, limits (`max_replans`, `max_steps`). The Planner
picks a subset per run and never adds; only the contract travels with a plan.

**Agent spec** — a named agent a plugin registers (`AgentSpec`): description
(for the Planner), instructions, result type, tier, toolsets (its ceiling),
budget `(max_turns, tokens)`, temperature. A declaration, not a Pydantic AI
agent; the core runs it through the Harness. The spine's form of the
**agent declaration**.

**Toolset** — the unit an action's contract grants and an agent spec names:
a set of tools under one name, `<plugin>.<thing>` (`backend.logs`) or
`core.<thing>` for the core's own (`core.memory`, `core.memory_write`,
`core.skills`, `core.shell`, `core.workspace`, `core.repos`, in
`friday/kernel/toolsets/`), which any plugin may grant. `core.memory` reads and proposes;
`core.memory_write` adds, updates and deletes — its own toolset so an agent
can hold the reads without the writes (build-the-spine ticket 22). Declared as a *toolset spec*. Not a Pydantic AI toolset,
though `core.workspace`'s factory hands one back.

**Workspace** — Friday's own scratch folder for one task,
`/tmp/friday/<task_id>/` (`core.workspace`): read, write, edit and list
freely inside it, nothing outside (pydantic-ai-harness `FileSystem(root_dir=…)`).
The one place Friday writes; lost on reboot by design. `core.shell`'s
`save_to` writes long output there.

**Read-command allowlist** — the core constant (`READ_COMMANDS`,
`KUBECTL_VERBS`, `REFUSED_FLAGS`, `SECRET_FILES`, `SECRET_DIRS` in
`friday/kernel/toolsets/shell.py`) deciding what `core.shell` runs, the same
for every plugin: `shlex`-parsed, `|` only between listed commands;
operators, substitutions, write flags, `kubectl`'s credential/server flags,
the `secret` resource (and `--raw`, `-f`, `-k`, `*-file` templates),
process environments and credential paths (case-insensitive, for the
commands that print content; grep's pattern word exempt, found as getopt
would) refused; credential files excluded from every
`grep`, which may not `--include` or `-R`. A guardrail by name, not a
boundary.
Off the list is **refused, not queued** and written to `audit_log`; the
operator widens it by commit.

**Toolset spec** — a named set of tools a plugin registers (`ToolsetSpec`):
description, a factory that builds the tools per run, the MCP reads it may
make (`{server: TOOLS}`) and the domain type it reads.

**Run context** — what the core hands a toolset factory once per run
(`RunContext`): task id, the domain value, the evidence read so far, when
the reporter spoke (`reported_at`), and each declared MCP server narrowed to
the toolset's reads — filled per toolset by the core (`reads_for`), so two
toolsets in one run never share reads. Not Pydantic AI's `RunContext`, which
only `harness.py` names.

**Toolset file** — one file per data source under `plugins/<domain>/toolsets/`
holding both the client that reaches out (a *source*) and the tools a model
calls over it (`backend.logs`, `backend.db`). The only plugin code that may
start a process or call through a tool server. (Build-the-spine ticket 09;
`plugins/backend/sources/` folded here. Reading, searching and listing a
repository — `backend.code`/`backend.docs` — moved into the kernel as
`core.repos` in ticket 23, generic over the sdk's `RepoRoom`.)

**Running version** — the image tag a service is deployed at, which is its
release tag. Amended 2026-09-30: **the model finds it**, not a per-run
cache. `core.repos`'s `read`/`grep`/`glob` take an optional `ref` (a tag,
branch or sha); the model calls `release_status` or reads a pod's own image
tag (ticket 28) and passes the result in. With `ref`: `git show`/`git
grep`/`git ls-tree` at it. Without: the checkout, said in `not_checked`.
(`RunningVersion`/`ReleaseSource`/`backend.release`/`Evidence.resolve_ref` —
a per-run cache the operator rejected — are deleted.)

**Repo** (a tool argument) — one of the room's `backend.project` rows by
name (`Placement.projects`, exposed to `core.repos` as `Placement.repos()`,
the sdk's `RepoRoom`); every `read`/`grep`/`glob` call takes one and refuses
any other.

**Outcome** (`friday/sdk/actions.py`; named `Action` until build-the-spine
ticket 06, the file moves to `friday/kernel/spine/plan.py` in 16):
what a graph returns about a task — `Ask`, `Reply` or `HandOver` — never a
side effect. Every task first **fills in** what the message carries and
**checks** it against the type's rules; then its graph decides. `Ask` goes to
the reporter as a question; `Reply` answers the reporter in the operator's
name and **waits for approval**; `HandOver` goes to the operator only and
never reaches the reporter. A task missing something it cannot work without
has to say so: for a **single-node type** (e.g. `ops.request_permission`) required-ness
is read off the parameter type and a precondition belongs in that gate, not in
the last node's else branch. **`trace_problem` is no longer gated on findability**
(board `build-the-loop`, 2026-09-27; ADR 0002): its diagnose loop reads log,
code and docs and calls `ask_reporter` only when genuinely stuck, so a missing
correlationId/curl no longer blocks opening an investigation — the reversal of
the old "a correlationId *or* a curl makes a request findable" precondition.
`trace_problem`'s `Report` produces a `Reply` (a brief) today.

## Plan

What one pass of a task will do (`Plan`, `friday/kernel/spine/plan.py`):
task, action, `plan_version` (1, +1 per replan), `replaces` (the hash of the
version it replaced), the action contract copied in, a one-sentence goal, and
a **straight list of steps** — no branch, no parallel; a change of direction
is a replan. Its **plan hash** is sha256 of the canonical JSON of the whole
plan, contract included. Built in build-the-spine ticket 06; every version,
frozen or refused with its gate errors, is a row of `plans` since 14.

**Brief** — an `agent` step's instruction from the Planner: what the step must
establish and what the reporter gave, never how to investigate (that is the
agent's own instructions). (Build-the-spine ticket 20.) The Planner writes
no grant: the step's toolsets are always `contract ∩ ceiling`, filled by code
(ticket 22).

**Step** — one entry of a plan, one of four core-owned types: `agent` (a named
agent, the toolsets granted to it, a brief), `ask` / `hand_over` (the Planner
deciding that up front), `draft` (the core responder writes the `Reply`).
`reads` names the earlier steps whose stored result it is handed. Exactly the
last step is terminal (`draft`, `ask`, `hand_over`).

**Step key** — a step's memo key: hash of its fields minus `id`/`reads`, the
keys of the steps it reads, and the task's placement identity. Results are
stored by `(task_id, step_key)`, so a replan's identical step reuses its
result, a changed step re-runs with everything reading it, and a changed
placement matches nothing old.

**GatePlan** — the plain-code check every plan version passes before it runs
(`friday/kernel/spine/plan_gate.py`): shape (stops on fail), then contract
(step types, agents, toolsets ⊆ contract ∩ the agent's ceiling) and
`max_steps`, every error gathered — **refused, never clipped** — then frozen
with its hash.

**Planner** — the one core agent that writes every plan version
(`friday/kernel/spine/planner.py`), for every action, one-step cases
included. Given the intake context, the action's contract and `planning`,
and each allowed agent's name, description, result shape, terminal tools and
budget; reads only — the read tools of `core.memory` and `core.skills`. A plan GatePlan refuses
goes back in the same conversation with its errors, `PLAN_REWRITES` (2)
times, then **planner_failed** — a `HandOver` (`PlannerFailed`) carrying
every version, its errors and what the Planner read; told apart from a
`hand_over` step the Planner chose. Not an agent spec: it has no terminal
tools and no contract grants its toolsets.

**Plan version row** — one row of `plans` (`task_id`, `version`, `replaces`,
`cause`, `placement`, `body`, `hash`, `gate_errors`, `pass_no`), written once:
a crash that re-runs the Planner keeps the first. `cause` is what made the
Planner write it — `first`, `replan` (an agent's `Replan`), `reply` (a reply
moved the placement) or `hand_back`; `replan` and `reply` count toward
`max_replans`, from the last `hand_back` on.

**Replan** — a new plan version for the same action because an agent's step
pointed the wrong way (the `replan(reason, found)` terminal tool → `Replan`).
The Planner starts a fresh conversation with the current plan, the stored
results of its finished steps and the reason; the new version `replaces` the
old hash, identical steps keep their results by step key, and it gets its
own rewrites. Counted against the contract's `max_replans`. Not a
**re-triage**, which leaves the action.

## Spine

The one path every task of a spine action runs (`friday/kernel/spine/`,
build-the-spine ticket 14): Intake → acknowledge → plan → run → deliver. Only
`backend.trace_problem` is on it until tickets 15–16.

**Pass** — one short durable workflow of the spine, `task-<id>/pass-<n>`
(`n` = `tasks.pass_no`, +1 in the same transaction as every move into
`pending`; `tasks.pass_cause` says why: `first`, `reply`, `hand_back`,
`reopen`). Each stage is one DBOS step, so a crash resumes the same pass and
nothing done is done again. An `Ask` ends the pass — no workflow waits on a
person — and the reply starts pass n+1: an unchanged placement continues the
asking step from its continuation point, a changed one replans (cause
`reply`). A reporter message newer than the pass's Intake when it ends in an
`Ask`: the question is not sent, pass n+1 starts.

**Acknowledgement** — the action's `acknowledge(intake)` hook, queued once
per task after Intake, without approval (`backend.trace_problem`: "đang xem
log của <service>…"). A later pass never sends a second one.

**Deliver** — a pass's last step: outbox rows and the task's move in one
transaction, refused for a pass the task has left, so a re-run queues
nothing twice. `Ask` → the question as the agent wrote it, redacted
(`MAX_ASKS_PER_TASK` (3) since the last hand-back, then `HandOver`
`asks_exhausted`); `Reply` → the reply and its approval card; `HandOver` →
`needs_human` with the reason.

**Hand-back** — the operator moving a task from `needs_human` back to
`pending`: a fresh run on the old ground. Replans and asks count from here,
the Planner writes a new plan (cause `hand_back`, not counted) from the last
one and its results, and a stored `HandOver` is never reused — its step runs
again. Not a **reply**, which continues.

## Graph

How a workflow of more than one decision is made. A **node** is
`async (state, deps) -> result`; an **edge** may carry a predicate, and the
first that holds is taken. Deterministic Python.

- **Node 0, `prepare`**, extracts and validates and runs fresh on every pass;
  it is never checkpointed. Its output is what state is discarded against.
- A graph **checkpoints after every other node**, keyed on its **version** — a
  digest of node names and edges — so a changed shape never inherits old
  results.
- **One invoke** (`adapter._invoke`): retries over an explicit exception list
  with doubling backoff, any other exception turned into a result — no clock
  (ticket 17). A result is an `Action` (ends the run) or an **envelope** —
  `status` (`ok`, `empty`, `skipped`, `error`; `timed_out` only on rows from
  before node clocks went) and `reason`. A
  node that cannot do its job **skips out loud**. Each attempt is a **node
  run** row.
- A node that calls a model names its `agent`.
- An agent is a node inside a graph, never the thing driving it; which agent
  and server a node gets is composition, handed in through `deps`.

## Core Intake

`intake()` (`friday/kernel/spine/intake.py`), the same three steps for every
action, no model, no network: **seed** (`IntakeSeed`: `request_text` over every
reporter turn, `reported_at`, raw `Hints` — every uuid, every `ArtifactRef`)
→ the domain's **enricher** → **retrieve** (memory + skills by the
*retrieval keys*). Its output, `IntakeContext` (`friday/sdk/intake.py`), is
what every agent in a run receives, with the enricher's value as `domain`.
What a hint *means* (the curl, the correlationId) is the domain's call.
The seed also keeps the turns apart (`db.original_turns_for`): backend takes
the env from the newest turn that pastes a URL, so a reply moving dev → prod
is another case.
(Build-the-spine ticket 07.)

## Placement identity

A task's staleness key: the domain type's `IDENTITY` fields, read in order by
`IntakeContext.identity`. Backend's is `(env, service, clone_path, repo_path)`
on `Placement` (`plugins/backend/placement.py`); `release_tag` left it in
build-the-spine ticket 07 (it was never filled). A domain with no enricher
(`ops`) has identity `()`, so a reply always continues. Intake runs fresh every
pass and is never checkpointed; when the reporter answers an `Ask`, the run
resumes only if the identity is unchanged, and discards the running
investigation if it differs (a reply that moves env/service is a different
case, not a continuation). Memory, skills and the hints (a correlationId, the
curl's artifact) changing do **not** invalidate — they sit outside `IDENTITY`.
Introduced on board `the-graph-becomes-a-loop` (ticket 01) and wired on
`build-the-loop`.

## Retrieval keys

What core Intake matches memory on besides the text: the named keys the domain
type's `retrieval_keys()` returns (backend: `{"service": …}`, empty while the
service is unresolved). `db.case_memories(channel_id, keys, text)` matches a
runbook whose `when` list of the same name holds the value (`when.service`),
or whose `keywords` appear in the text, and the newest findings whose data
carries every key with the same value. Only core `intake()` calls it; what
Intake cannot know yet the agent fetches itself. (Build-the-spine ticket 07;
replaced `diagnose_memories(service=, error_code=, path=)`.)

## Source, check, node

Three layers. A **source** reads one kind of thing and decides nothing; it
lives in its data source's **toolset file** (`plugins/*/toolsets/`, since
build-the-spine ticket 09), the only plugin code that reaches an outside read
surface. A
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
model settings, recording hooks, the budget (agent turns and tokens),
attempts, and the rule that any failure becomes work for a person rather than
silence. What differs per agent is its **agent declaration** — tier,
temperature, `(max_turns, tokens)` — and what it is built with: instructions,
tools, output shape. Structured answers come back through a generated **answer tool**,
checked in-process, with one correction turn.

**`run_agent`** (`friday/kernel/harness/run_agent.py`, build-the-spine ticket
10) runs an **agent spec** on its tier with the toolsets contract ∩ the spec's
ceiling, built per run from the run context. It returns the spec's result or
an outcome from a terminal tool; a run its budget stopped is a `HandOver`
`budget_spent`, any other failure raises `AgentRunFailed`.

**Terminal tool** — a core tool that ends an agent's run with an outcome
instead of its result: `ask_reporter` → `Ask`, `hand_over` → `HandOver`,
`replan(reason, found)` → `Replan` (same action, new direction),
`retriage(reason, found)` → `Retriage` (another action's work). Every agent
gets them; the plugin declares only `result`. `ask_reporter` is dropped when
the action contract has no `ask` step.

**Continuation point** — a stored `Ask` an agent raised: it carries the run's
message history (plain JSON) and its `Evidence`. Passed back to `run_agent`
with the reporter's reply as the brief, the run continues from it — nothing
already read is read again, `Lnn` ids keep their meaning. Not a mid-loop
checkpoint: a crashed step re-runs from its start. Continued only by a later
pass; the pass that stored it reuses it.

**Grounding check** — an agent spec's `check(result, evidence)`: the reason
its answer is void (a ref naming no line read, a conclusive answer with no
rival ruled out, nothing read at all), or `None`. `run_agent` turns a reason
into `HandOver` `ungrounded: …`, so a void answer is never drafted from.
`backend.diagnose`'s is `plugins/backend/agents/diagnose.py`.

**Runner** — `run_plan` (`friday/kernel/spine/runner.py`, build-the-spine
ticket 12) walks a frozen plan's steps in order. A step with a stored result
is skipped and its readers get that result; else the step runs (up to
`STEP_ATTEMPTS`, then `HandOver` `step_failed`) and its result is stored.
`Ask`, `HandOver`, `Retriage` and the draft's `Reply` stop the plan; a fresh
`Replan` asks the Planner for the next version until `max_replans` is spent
(`HandOver` `replans_exhausted`).

**Step result** — what one step came to, stored in `step_results` at
`(task_id, step_key, pass_no)` by the runner alone and never rewritten; the
newest pass's row under a key is the one read. A stored step never runs
again within its pass, so a reused `Replan` is data for its readers, not a
new signal. From an earlier pass, a stored `Ask` is continued (the Planner's
own `ask`, answered, is a replan) and a stored `HandOver` runs again. A
stored `Ask` keeps its message history and its `Evidence`.

## Install fact and knob

An **install fact** describes this machine — who the operator is, where the
db lives, which servers and channels — and lives in `config.yaml`. A **knob**
tunes how Friday behaves and is the same on every machine, so it is a named
constant beside the code that uses it, pinned by a test; changing one is a
commit (board `domains-plug-in`, ticket 07). A knob left in `config.yaml` is
refused at load.

## Model tier

A named block under `tiers:` in `config.yaml` — provider key, endpoint, model
and provider settings (`max_tokens`). Code picks a tier by name from an agent
declaration (`AgentDeclaration.tier`); an undeclared tier refuses the boot.

## Attempt

Doing the same failed thing again: a provider 429/502, an answer of the wrong
shape, a failed send, a model request past its `request_timeout_seconds`.
Not progress, so not a turn. Core constants, the same
for every agent: `PROVIDER_ATTEMPTS`, `OUTPUT_CORRECTIONS`, `OUTBOX_ATTEMPTS`,
`STEP_ATTEMPTS` (a whole spine step, in the runner).

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
the one write path — the **kernel's** (`friday/kernel/memory/write.py`, ticket
16), not the store's, so a dumb store cannot skip it. A proposed memory waits in
`memory_candidates` for the same mark that confirms a classification.

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
in `friday/kernel/domain/states.py`.

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

The `Outcome` for "cannot conclude". Its reason is the system's own finding,
quoted to the operator; the task moves to `needs_human` and the pool
announces it once. Code-authored reasons start with a countable word:
`planner_failed`, `step_failed`, `replans_exhausted`, `asks_exhausted`,
`ungrounded`, `budget_spent`. **Not** "handled by the operator", which is a person
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
back only as far as the **lookback** — `MAX_MESSAGE_AGE_SECONDS`, the same number that
marks a turn `outdated`.

## SDK

`friday/sdk` — the bottom of the stack the rest builds on: Protocols and
dataclasses, plus the pure dependency-free values and helpers everything shares.
Holds the workflow port (ticket 06), the plugin contracts (ticket 10), and —
since ticket 19 folded `friday.domain` away — the workflow actions
(`Ask`/`Reply`/`HandOver`), the validation DSL, the prompt primitives, `scrub`
and the memory `Origin`. It imports **nothing of ours**.

## Kernel

`friday/kernel` — owns the invariants and **names no plugin**. The
kernel-consolidation (tickets 19–21) folded every application module in here:
the **registry**/**plugin_host**, the **domain vocabulary** (`domain/`: models,
states, conversation, triage outcomes, the memory-write guard), the graph home
(`dag/`, including the DBOS `adapter.py`), the **harness/** (was `agent/`),
**pool/** (was `tasks/`), and `memory/`, `outbox/`, `triage/`, `extraction/`,
`responder/`, `inbox/`, `ops/`, `text/`, `tools/`, `providers/`, `config.py`. It
imports `sdk`, itself, and the concrete `friday.store` (the one standing
exception `test_dependency_rule` names — inverting the store behind a `Store`
Protocol in the sdk is DESIGN-v2 deferred work; ticket 16 split the store into
repositories and moved its invariants into the kernel but did not build that
Protocol). `friday/` now holds only `sdk/`, `kernel/`, `store/` and `plugins/`.

## Plugin, PluginAPI, register(api)

A **plugin** is a `Plugin` value (`id`, `register`, `requires`, `config`) plus a
`register(api)` function — no base class. The kernel hands `register` a
**PluginAPI** (the registry, by shape), and the plugin calls `api.task_type(…)`
/ `api.memory_kind(…)` to contribute. Adding a capability is adding a plugin,
not editing the core.

## TaskTypeSpec, MemoryKindSpec

How a plugin declares a task type (`name`, `params`, `extractor`, `graph`,
`deps`, `needs`) and a memory kind (`name`, `data`, `writers`, `cardinality`,
`injected`). Both **trimmed to the fields the registry uses today** — deferred
fields are added on their trigger, each with a test, so no inert field pretends
to be a rule.

## Task-type registry

`friday/kernel/dag/registry.py` — the one place a task type is known (ticket 11),
filled by each type's `register()` (`friday/kernel/dag/task_types.py`). It replaced the
hand-maintained `PARAMS`/`DECISIONS` maps, the extractor map and the router's
`_graphs`. `decision_params()` is the old `PARAMS`; `decisions()` the old
`DECISIONS` (types + `skip`). The router reads it and **names no task type**;
triage builds its closed-set schema from it at boot (`make_decided`); the store
takes the decision set as an argument so `domain` never reaches up.

## Memory-kind registry

`friday/kernel/memory/registry.py` — the one place a memory kind is known (ticket 12),
filled by `register_all_memory_kinds()`. It replaced the hardcoded `_READERS`/
`_WRITERS`/`MEMORY_DATA` maps. A kind registers a `MemoryKindSpec` (writers,
data, cardinality, injected); `MemoryKind` is now a **validated string**
(`validate_kind`), while `ModelMemoryKind` (the five the tools expose) stays a
closed enum. **Readers are inverted** — each reader declares the kinds it needs
(`register_reader`), so a kind names no reader (DESIGN-v2 §9.2); `readers_for`/
`domain_kinds` derive from that, and `injected` gates what reaches a prompt.

## Dependency rule

The one direction the restructure rests on, enforced by
`tests/test_dependency_rule.py`: `sdk` imports nothing of ours but `domain`,
`kernel` imports `sdk`, a plugin imports `sdk` only. Plus **the kernel names no
plugin** — no plugin import and no task-type or pack-kind literal in
`friday/kernel`.

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
