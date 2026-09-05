# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status

Running. It ingests Discord mentions, classifies them, opens tasks, asks for
missing details, and sends approved replies as the watched account. Roughly
9,400 lines under `friday/`, on a single branch (`main`), with a passing suite.

The design is settled and written down — see **`docs/DESIGN.md`**, the source of
truth for what this is meant to become. Read it before adding anything
non-trivial. Decisions recorded there were reached deliberately; if you think
one is wrong, raise it rather than quietly building something else.

Work is broken into tickets under `.scratch/discord-mention-triage/issues/`,
derived from `docs/SPEC.md`. Tickets 01–17 and 23–27 and 29–33 are done; 07
was superseded and reopened as 28, and 28 is now retired in favour of 32 and
33. **Open: 18–20 only** — the board's own repo and its UI, deferred by choice.
34–45 are done. All of them came out of watching real threads rather than
reading code: the reporter replied and nothing could hear the answer, sent the
details in a second message and nothing read it, asked what a correlationId is
and nothing could explain, and the operator answered by hand while the agent
went on asking. Each ticket names what blocks it; work the frontier.

A second board, `.scratch/every-task-is-a-graph/`, holds the spec and tickets
01–13, and all thirteen are done. 01–09 folded two packages into one engine —
every task type a graph, `prepare` as node 0, node agents reporting through
tools — and the old loop package is gone, split between `friday/tasks/` (the
pool) and `friday/dag/`. Its spec's D1–D18 are the rationale; the tickets
reference them rather than repeating them.

10–13 came out of reviewing that work rather than planning it, and three of
them were real bugs the board had shipped: the composer replied to reporters
in a node's voice with an unreviewed diff in it, a one-node graph's only
hand-over never reached the operator, and an approved patch outlived the task
it belonged to. Worth knowing when reading the board: nine tickets' worth of
green suite did not catch any of the three, and one of them was pinned as
correct by the board's own test.

The two paragraphs above go stale faster than anything else in this file.
Check them against the `**Status:**` line in each ticket before trusting them.

Keep the split honest: this file describes what *exists*, `docs/DESIGN.md`
describes what is *agreed*. Do not document intent here as if it were
implemented — and when a design decision is reversed, **this file is the one
that goes stale silently**, because nothing breaks when it is wrong. It has
been wrong before, badly enough to tell a fresh reader that `friday/` did not
exist yet. If you change a load-bearing decision, correct it here in the same
commit.

## Environment

- Python **3.13** (pinned in `.python-version`), managed with **uv**.
- Runtime dependencies are in `pyproject.toml`; the venv lives in `.venv/`.
  Deliberately few, and kept that way: `openai-agents` in particular is here
  for speed, not for keeps — see the seam rule under Architecture constraints.

```bash
uv sync                 # create/update .venv from pyproject + uv.lock
uv run run_agent.py     # run the entrypoint
uv add <package>        # add a dependency (updates pyproject.toml and uv.lock)
uv run pytest -q        # the whole suite; -k <expr> for one test
```

Persistence is **SQLAlchemy 2.0 async** (`friday/store/schema.py` holds the mapped
classes, `friday/store/db.py` converts them to and from the domain dataclasses) with
**Alembic** migrations in `migrations/`. `run_agent.py` upgrades to head at
startup, before anything opens the database.

```bash
FRIDAY_DB=/tmp/new.db uv run alembic upgrade head          # build a clean db, then
FRIDAY_DB=/tmp/new.db uv run alembic revision --autogenerate -m "what changed"
uv run alembic upgrade head                                # apply to the real one
uv run alembic current                                     # where this db is
```

Migrations run **transactionally** (`transactional_ddl=True` in
`migrations/env.py`). Alembic assumes SQLite cannot do DDL in a transaction;
SQLite can, and the difference is not academic — a migration that added a
column and then failed before stamping left the schema ahead of the version,
and `alembic upgrade head` died on `duplicate column name` at every subsequent
start. `run_agent.py` migrates before anything opens the database, so that is
a boot loop, not a warning.

Autogenerating against the live database is safe again. It was not: the
original `data/friday.db` was built by hand-written DDL before Alembic existed
and then stamped, so reflecting it yielded ~27 cosmetic differences — `TEXT` vs
`VARCHAR`, `server_default`s the models do not declare, a different column
order — every one of which would have landed in a migration that changed
nothing. That database was wiped on 2026-09-01 at the operator's request and
rebuilt from `alembic upgrade head`, so it is now the migrations' own output
and `compare_metadata` reports zero differences.

**A throwaway database is still the safer habit**, because this only holds
while nothing touches the schema by hand again:

```bash
FRIDAY_DB=/tmp/new.db uv run alembic upgrade head
FRIDAY_DB=/tmp/new.db uv run alembic revision --autogenerate -m "what changed"
```

The database path comes from `config.yaml`, not `alembic.ini` — `FRIDAY_DB`
overrides it. `tests/test_migrations.py` fails if `schema.py` and the migrations
stop describing the same database; tests build their schema from the models,
the service builds it from migrations, and nothing else keeps those in step.

Prefer `uv run ...` over activating the venv manually, and let `uv add` edit
`pyproject.toml` rather than hand-editing dependencies.

## Running it on a server

```bash
docker compose build && docker compose up -d
docker compose logs -f
```

Secrets arrive at runtime from `.env`, never baked. `config.yaml` is mounted
read-only, so changing a model or a threshold is a restart rather than a
rebuild. The database is on a named volume — without it a redeploy loses the
cursors, and the sweep either re-reads history or misses the gap.

The board is unauthenticated by design and shows every captured message and
every model prompt, so it is published to the host's loopback only. Reach it
with a tunnel:

```bash
ssh -N -L 8086:127.0.0.1:8086 you@your-vps
```

## Layout

What is actually on disk.

| Path | Contents |
| --- | --- |
| `run_agent.py` | Composition root — the only place adapters are constructed, and the only place the asyncio tasks are started. It asks each module to build itself; it reads no agent's knobs |
| `serve_board.py` | The board alone, against the live database, without connecting to Discord |
| `init_channel.py` | One-off: create a channel's context file for the operator to fill in |
| `poke.py` | Put a message in the queue by hand, as if somebody had reported it. The only way to test end to end without a second Discord account — the watched account's own messages never open work, deliberately (ticket 37), so a self-mention does nothing. Skips the gateway and the scope check and nothing else; `FRIDAY_DB` points it at a throwaway copy |
| `config.yaml` | Per-agent models and caps, channel whitelist, thresholds, MCP servers, and the sensitive words that keep a message away from the model |
| `friday/config.py` | Loads `config.yaml` and resolves `${VAR}`. Outside the packages because it is read before any of them |
| **`friday/domain/`** | The vocabulary, and nothing else: `models.py` (every dataclass), `conversation.py` (what counts as one exchange), `states.py` (`TaskState`, `OutboundState`, and the legal transitions), `actions.py` (`Ask`/`Reply`/`HandOver`, what a decision about a task comes to), `validation.py` (the rule engine, one call site) |
| **`friday/store/`** | `schema.py` holds the mapped classes, `db.py` is the only store and converts at the edge — nothing above it knows SQLAlchemy exists |
| **`friday/agent/`** | What it takes to call a model, and nothing about what to call it for: `harness.py` (the only module that may import the SDK), `instruction_prompt.py`, `skills.py`, `mcp.py`, `llm_log.py` |
| **`friday/memory/`** | What is kept between tasks, in tiers that never mix: `observations.py` (staged), `notes.py` (promoted, and only by an approved outcome), `channel_context.py` (per-channel YAML), `verdicts.py` (the operator marking a classification right) |
| **`friday/ops/`** | Alive and safe, deciding nothing: `liveness.py`, `redact.py`, `api.py` |
| **`friday/text/`** | `transform.py` splits code out before cleaning the prose; `param_hygiene.py` cleans one value. Decides nothing |
| `friday/inbox/` | Deep module: `stream()`, `sweep_once()`, `tally()`. Gateway, backfill, cursors and dedup are implementation |
| `friday/providers/` | `Provider` protocol; `providers/discord/` holds `user.py` (the account), `bot.py` (approval cards) and `normalise.py`. Its `__init__.py` is empty on purpose |
| `friday/triage/` | Classification and nothing else, its sensitive-word prefilter, and the loop that polls untriaged messages |
| `friday/extraction/` | Everything a task knows, lifted out of what the reporter wrote. One extractor per task type, each owning its prompt, schema and model |
| `friday/dag/` | `engine.py` is the graph framework — nodes, edges, checkpointed resume — and `state.py` what a run accumulates; the package's `__init__.py` is empty on purpose. `dag/prepare.py` builds the entry node every graph shares and holds the fill-and-validate mechanism it runs. `dag/router.py` maps a task type to a graph. **Every type now gets the same one-node graph** |
| `friday/tasks/` | The pool: pulls pending tasks and hosts their graphs. Stand down, announce, host the graph, act on the outcome — nothing about what a graph decides |
| `friday/tools/` | Every tool an agent may call, one module per subject — asking (`clarify`, `ask_for_fields`), classifying (`classify`), reaching a skill (`fetch_skill`, `search_skills`, `describe_skill`, `read_skill_file`), remembering (`memory`, shape only: it calls four `Database` methods that do not exist yet and no agent has it — ticket 09 of `.scratch/nothing-runs-unmeasured/`). A test asserts the list — all twelve, factories built rather than skipped — and forbids declaring one anywhere else |
| `friday/responder/` | Drafts a reply in the operator's voice |
| `friday/outbox/` | Nothing is sent by a caller: it is a row, and one loop delivers it |
| `friday/board/` | The read-only page on `:8086`; its JSON API is `ops/api.py` |
| `migrations/` | Alembic revisions |
| `tests/` | Driven through two seams: a fake `Provider` and a scripted model transport |
| `docs/` | `DESIGN.md`, `SPEC.md`, `agents/` |
| `.scratch/` | Local issue tracker |

Packaging: **explicit `__init__.py`**, not namespace packages. That is a
statement about PEP 420, not a licence to put implementation in `__init__.py` —
importing any submodule runs the parent's `__init__.py` first, so whatever
lives there is paid for by every import of the package.

There is no `procedures/`, `permissions/` or `hooks/` package, and their
absence is a decision rather than an omission. (`memory/` was on that list
until it existed — the six packages above were carved out of twenty-two loose
modules once flat stopped scaling, which is the same rule applied at a later
size, not a reversal of it.) The node vocabulary did come back, but as
`friday/dag/` rather than a `nodes/` package: a node is a function in the
graph that owns it. **Build one of these when a second caller appears, not
before.**

`tools/` **was** on that list, on the argument that a tool belongs beside the
state it touches because that is the only place its guard can be enforced.
That argument does not survive contact with the question "what can the agents
actually do?" — which has to be answerable, and was not. The tools were spread
across four modules that each owned part of the answer, and two of them were
invisible to a `grep` for `@tool` because they are wrapped by calling
`tool(fn)` after `__doc__` is assigned. Nor was the guard argument true: what
gates `apply_fix` is `needs_approval=True` on the decorator, which travels
with the function; the captures are per-run dataclasses that travel with it
too.

So **every tool lives in `friday/tools/`**, one module per subject, and
`tests/test_tools.py` enforces both halves of that: the list of tools is
asserted rather than described, and no tool may be declared anywhere else.
Both spellings are checked by reading the syntax, since grep sees only one of
them. A tool that needs something injected — `fetch_skill` a skill library,
`ask_for_fields` one type's field names — stays a factory; that is a
different thing from living somewhere else.

**The listing builds the factories rather than skipping them**, and that is
the second time this test has had to learn the same lesson. It scanned
`vars(module)`, which a tool living in a closure never reaches, so its
asserted list held three of twelve while a *second* test named five more by
hand — the answer to "what can the agents do?" existing, but split across two
lists that could not see each other. One list now, and
`test_every_factory_is_registered_here` fails if a new factory is not built
into it.

**`harness.tool` is not `function_tool`.** It wraps it and sets one default
for every tool here, because seven call sites remembering a keyword is six
chances to forget: `failure_error_function`. A tool whose *body* fails tells
the model "unavailable, carry on" rather than the SDK's default, which formats
the raw exception — a route out for a path or a credential that `_settle`'s
`scrub` never sees — and then asks the model to try again, which after a write
that may have landed is how a row is recorded twice.

**A `ModelBehaviorError` is exempt and keeps the SDK's own words**, because it
is the one failure the model can fix: bad JSON and schema violations are
raised before the body runs, so nothing was written and the only recovery is
to emit the call again correctly.

**Triage stops on what it recorded, not on the first tool output.**
`stop_on_first_tool` looks right for an agent whose answer is a tool call and
is not: it ends the run at the first tool's *output*, and a
`failure_error_function` return value is a tool output the SDK cannot tell
from a success. So the "try again with valid JSON" the exemption above exists
to deliver became the run's final answer, and the one party who could act on
it never saw it — a mention the model had all but classified became work for a
person. `harness.stop_when(predicate)` moves the terminator to what actually
means answered: `capture.decided is not None`.

**The correction budget is one turn, and it is `max_turns`.** A bad call
spends a turn, so `max_turns: 1` plus the one `run(extra_turns=1)` adds gives
exactly one retry; a second bad call overruns, the harness turns that into a
`last_error`, and the mention lands where every other triage failure lands. A
model that cannot get its own schema right twice will not on the third go, and
this is the highest-volume path in the system.

**`docstring_style` is deliberately not pinned**, which is the opposite of
what this file said for one afternoon. Detection returns google for every
docstring here and falls back to google when it scores nothing, so the pin
changed no schema — while a `:param x:` docstring under a google pin loses its
descriptions that auto-detection reads correctly. The pin could only break the
case it existed to protect. What guards it is a test: every field of every
tool carries a description, whatever produced it.

**`ToolContext` is the SDK's own `ToolContext`**, not `RunContextWrapper`,
which it aliased for months. The runtime passes the former, carrying
`tool_name`, `tool_call_id` and `tool_arguments`; the alias hid all three.
It may point at either of those two classes and **at nothing else, not even a
subclass of them**: `function_schema` decides whether the first parameter is
the run context by identity, so a subclass silently becomes a parameter the
model must fill. Tested, because nothing else would say so.

**Four tools reach a skill, and the split is by what the agent already knows.**
`fetch_skill` when it has the name — from the catalogue, which is still in the
prompt and still the common case. `search_skills` when it does not: the
catalogue is matched by eye, so a skill named `deploy` described as "release a
build" is invisible to an agent looking for "rolling out". `describe_skill` for
the metadata behind one line — the mutability tag and the file's path — without
paying for the body. `read_skill_file` for the file a body linked to, by the
path the body wrote. Its fourth search rank splits the **query**, not the
corpus: the tool asks for a phrase, and splitting the corpus instead could only
ever match an infix of the name, because a query inside a description's word is
already inside the description and caught a rank above.

## Architecture constraints

Load-bearing decisions from `docs/DESIGN.md`. Violating one is a design change,
not an implementation detail:

- **One process, one container.** Bot gateway, user gateway, worker, and the
  web server on `:8086` are all asyncio tasks in a single event loop. This
  follows from SQLite: multiple writers over a shared volume means contention
  and locking bugs.
- **SQLite is the only state store**, including per-channel cursors
  (`last_seen_message_id`). Anything that must survive a restart goes in the DB,
  never in memory. Gateway session state is the exception and is deliberately
  not persisted: the library owns it, and cursors plus the sweep cover restarts. **DB access must be async**
  (`aiosqlite` or a thread executor) — a blocking call on the event loop stalls
  the Discord gateways.
- **Two Discord identities in one process.** `discord.py` for the bot
  (`providers/discord/bot.py`), `discord-self` for the user account
  (`providers/discord/user.py`). The user side depends on a private API and is
  expected to break, so `discord_self` may be imported in that one module and
  nowhere else — a test enforces it. `providers/discord/__init__.py` stays
  **empty** for that to hold: importing any submodule runs it first, and a
  re-export there is eager, so one convenience import would pull the unofficial
  library back into the official bot and into normalisation.

  **`is_own` only knows one of the two.** It is decided on the user gateway as
  `author.id == me.id`, so everything the *bot* posts reads as a stranger's
  message. "Is this ours?" is `Database.we_sent` — matched on the sent id or
  the text, the second closing the window between the outbox posting and
  recording the id it got back — and the inbox calls it on every message.
  Without that call the bot's DM to the operator came back through the user
  gateway, and because a DM bypasses the channel whitelist it entered the
  triage queue: the agent classified its own liveness summary and sent the
  operator "Nothing I can do with this" quoting itself. `we_sent` was written
  for this and spent a week with no callers.
- **Inbound messages are deduplicated on `(provider, provider_message_id)`.**
  Two delivery paths (gateway and REST backfill) feed the same pipeline, so
  every handler must be idempotent on that key.
- **Never drop a mention.** Low confidence, turn-cap and token-cap breaches,
  refusals, classifier errors and the sensitive-word prefilter all route to
  `HITL` — never to a silent discard. A dropped mention is indistinguishable
  from correct operation.
- **Some messages must not reach the model at all**, and that is decided
  before the call, by `config.yaml`'s `sensitive_words` — pay, health records,
  credentials. A rule that runs first cannot be argued out of by a persuasive
  message. It **holds**, it does not skip: several of those words appear in
  ordinary reports ("token hết hạn rồi" is a bug), so the guarantee is that
  the *model* does not see it, not that nobody does. The operator adds to the
  list as they notice things, so it is configuration and a restart.
- **Every model call goes through Chat Completions**, so `base_url`, `api_key`
  and `model` are the whole of what it takes to move an agent to a different
  OpenAI-compatible provider. Not the Responses API: some providers reject
  parts of it, and one of them is the one in `config.yaml`.
- **Every model call is bounded and written down at one seam.** `_settle` is
  where `Runner.run` is called, so it is where a run gets its clock
  (`timeout_seconds` per agent, 60s by default — not the provider client's ten
  minutes) and where the call is handed to the recording sink. The sink is
  given to a `Harness` **at construction**, by the composition root, and never
  passed to `run()`: it was a `calls=` list on the call, three of the four
  callers forgot it, and `model_calls` held triage alone while the board
  described it as holding every prompt. A caller still names the `message_id`
  a call was about, and forgetting *that* loses a correlation key rather than
  the record. `AgentHooks` cannot do this job — `on_llm_start` fires after the
  decision to spend and `on_llm_end` after the money is gone.
- **What an agent reached for is written down beside what it was asked.** A
  prompt says what an agent was *told* and nothing about what it did — so
  which of the four skill tools it actually reaches for was a question nothing
  could answer, and it becomes an expensive one the day `mcp_servers` is not
  empty and a tool leaves this process with arguments a model chose. Tool
  calls travel the same sink as model calls, because a second seam is a second
  thing to forget, and part at the store, which is the only place that knows
  there are two tables. **A failure has to be told, not observed:**
  `_tool_failed` turns a raising tool into a message for the model, so
  `on_tool_end` sees an ordinary result and would file every failure as an
  answer — the harness marks it using the `agent` and `tool_call_id` the SDK
  passes every tool.
- **A hiccup is retried here and nowhere else.** `Harness._attempts` calls the
  provider up to `max_attempts` times with doubling backoff, and what counts
  as worth another call is an explicit list — connection errors, timeouts,
  429, any 5xx — never a guess from the message. A 400 is the provider saying
  the request is wrong, and asking again buys the same answer at twice the
  price. Running out is work for a person, and the reason says how many times
  it tried, because "429 slow down" alone reads as a moment while "gave up
  after 3 attempts" says the moment lasted.

  **The client's own retry is switched off** (`max_retries=0`), and that is
  not tidiness: `AsyncOpenAI` retries twice by default and says nothing, so
  the provider bills three calls where the record holds one. Every attempt
  gets its own row with its own prompt and its own cost — `attempt` is an
  ordinal, not a total — which is the only arrangement in which the record and
  the invoice agree.

  **`timeout_seconds` bounds the whole run, retries included**, so the pool's
  worst case stays what it was, and **one HTTP request gets a share of it** —
  `timeout_seconds / max_attempts`. Given the whole budget, the client's timer
  never fired first, the run-level one cancelled instead, and a cancellation
  is a `BaseException` the retry loop never sees: a hung provider spent the
  entire budget on one attempt while `APITimeoutError` sat on the retry list
  unable to fire. The cost of a share each is that one slow-but-working call
  fails where it used to be waited out, which is the right way round for
  agents that send one short prompt and read one short answer.
- **A ceiling refuses; it does not trim.** `daily_token_budget` is per agent,
  counted from the rows the agent actually wrote, so a restart does not
  forgive it and the number cannot drift from what the board shows. Reaching
  it means the call does not happen, and **what that becomes is the caller's,
  and they do not all answer alike.** Triage hands the mention to a person
  carrying the reason. An extractor raises `Refused` and its graph hands over,
  because the alternative is the failure this file already names for a type
  with no extractor: unfilled fields, then the reporter asked for the
  correlationId they wrote in their first message. The responder falls back to
  the plain template, which still goes out — a worse-worded message, not a
  lost one. The summariser skips a rebuild. Only the first two are hand-overs,
  and the paragraph that said all four were is what this sentence replaced. `max_tokens` is the other kind of limit and
  lives in `settings:`: it bounds one answer and comes back *truncated*, which
  is why a reply that hits it is a worse outcome than a call that never ran.
  Unset means no ceiling, deliberately: the heartbeat reports the day's spend
  either way, so the measurement is on from the first day and the ceiling is
  something an operator sets once they know what normal costs. The check reads
  the store before the call and **fails open** — a store that cannot answer
  this has already stopped the work by other means, and refusing on it would
  turn one bad read into every agent refusing at once.
- **`friday/agent/harness.py` is the only module that may import `agents`.** The SDK
  is here for speed, not for keeps, and that is only true while replacing it
  means rewriting one file. What other modules need — `tool`, `ToolContext`,
  `Hooks`, the MCP server types — is re-exported from there under names that do
  not mention the library. `tests/test_harness.py` fails if a second module
  reaches past it.
- **Workflows are deterministic Python, and an agent is a node inside one.**
  The route is classify → edge router → graph: which task type has a graph is
  `friday/dag/router.py`'s business, not the composition root's. The graph's
  *shape* is code — a model never chooses the next step. Durable resume is
  built (ticket 32): a graph checkpoints after every node, and discards its
  state when the task's parameters change, because a conclusion drawn without
  the correlationId is not a conclusion about the request that has one.
- **Every task type is a graph** (ticket 04). **Every** type gets
  one node — extract, validate, then ask for what is missing or hand over.
  `api_issue` had an investigation past that node and no longer does. `dag_for` never answers "no graph"
  for a type `PARAMS` knows about; there is no second way to decide what to
  do with a task any more, and `Pool._plan` asserts on the
  invariant rather than falling back to one. Build a multi-node graph when
  there are steps worth skipping, not before — a one-node graph is that
  principle under one name, not an exception to it.
- **The one message that goes out unread has a floor under it.**
  `auto_ask_for_details` sends a request for missing details with no approval
  step, on the grounds that what is asked is decided by code and only the
  wording is the model's. That was true of the *intent* and enforced nowhere:
  the responder's input carries other people's channel messages, so the single
  path with no human in it was also the one whose wording a model wrote from
  untrusted text and signed with the operator's name. It has already produced
  a promise nobody would keep, recorded in `Responder.draft`'s docstring.
  `friday/responder/check.py` is the floor — no link, no code block, near the
  template's length, and no promise — and a draft that fails it is replaced by
  the template and the reason logged.

  **One of the five rules binds only some questions, and saying so is part of
  the rule.** A draft must still name what the template named *where the
  template names something untranslatable* — `correlationId`, `curl`. Four of
  the seven questions this system asks name nothing of the kind ("what access
  you need", "which document you mean"), and for those there is no way to tell
  a faithful Vietnamese rewording from a different question. The other four
  rules carry those. A test says this out loud, so the paragraph cannot
  quietly become a stronger promise than the code makes. Blunt on purpose: a false refusal sends a plainer
  question, a false acceptance sends the operator's colleagues something the
  operator did not say.
- **Nothing is sent by the caller that decided to send it.** An outbound
  message is a row; one loop delivers it. Approval is enforced as a predicate
  in the query that selects sendable rows, not as a check each caller must
  remember — see `_NEEDS_APPROVAL` in `friday/store/db.py`.
- **Nothing takes a dangerous action, so there is no second gate.** There
  was one: `apply_fix` was marked `needs_approval`, the run stopped holding
  its own state, and `Pool.decide_pending_action` resumed or declined it. It
  went with the five-node `api_issue` graph that produced it — the operator
  removed that graph as a workflow nobody had described. `Harness.checkpoint`
  and `resume` are still there and still tested; nothing calls them. The
  outbox's approval is the only gate now, and every message waits at it.


- **An agent's voice is part of its own prompt, and what reaches a reporter is
  pinned on the `Reply`, not on a label.** Two agents write in the operator's
  voice because a person reads what they write under that name — the responder
  and the graph node that composes a reply — and each carries that text in its
  own module. Every other graph node is told the opposite. Triage and the
  extractors are told nothing about voice: 79% of the highest-volume prompt in
  the system was once instructions for writing replies it never writes.

  **This reverses "one persona, two families".** There was a `PERSONA.md` split
  by heading and a `Family` label deciding which agent read which section, and
  before that a per-agent `persona:` knob with three modes. The knob went
  because it rotted the first time an agent's job changed; the file and the
  label go now for a sharper reason: **the invariant they existed to protect
  was not protected by them.** "Only Responder-family agents produce text that
  reaches a reporter" had a test written in terms of the family, and that test
  passed throughout ticket 10 — a composing node with no agent sending a
  reporter an unreviewed diff — because there was no mis-assigned family to
  find. The anchor is `Reply` construction now: it is built in exactly one
  place, the `answer` tool, and a test says so. Reintroducing ticket 10's shape
  turns that test red and left the family test green, which is the whole
  argument in one run.

  What the old knob was guarding still holds and is still enforced, just by the
  prompts themselves: an agent carrying the voice writes `sản xuất` where
  `friday/domain/validation.py` wants `production`. The voice goes in
  `instructions`, never the per-call input — shared bytes at the front of a
  prompt are the ones a provider's cache reuses across calls. The two agents
  that share it hold two copies, deliberately: different jobs diverge, and
  sharing the text only postpones that.
- **Triage classifies and nothing else.** No parameters, no summary — a type
  and a confidence, through one `classify(task_type, confidence)` tool with
  a closed enum of types (`skip` stays its own tool: everything `classify`
  names opens work, and `skip` names the absence of it). It was
  `create_task`, and it creates nothing — it records a `Decided`; the task is
  opened by `TriageRunner._apply` and only sometimes. A tool name is an
  instruction to the model, so a model told to "create a task" believed it
  was doing something it was not.
  Everything a task knows is lifted out of the message by `friday/extraction/`,
  one extractor per task type, reading every message linked to the task. The
  tool schema is the enforcement: a tool parameter is an instruction to the
  model, so `correlation_id` in the schema *is* triage extracting whatever the
  prompt says, and a test pins that no triage tool asks for anything but a
  type and a `confidence`. Each type's description in the enum is read from
  its own `Params` class's docstring, so a fourth type is a fourth class, not
  a fourth tool.
- **A classifiable task type without a configured extractor is broken**, not
  degraded: it opens tasks with no parameters and asks the reporter for what
  they already said. `friday/extraction/`'s `EXTRACTS` and `PARAMS` must
  agree, and a test says so.
- **The unit is a turn, not a message.** A mention opens a turn — everything
  the same person goes on to say — and triage reads it once, when they have
  been quiet for `turn_seconds` and are not typing, or somebody else spoke.
  Turns are computed when read, never stored: when a message arrives it is not
  yet known whether the turn is over.
- **The operator's own message ends the work — unless they tagged themselves.**
  Their messages are always kept, because them answering is what closes a
  task, and they create no work: the agent answering its own replies is a loop
  that ran every minute in a real channel. The one exception is a message from
  the watched account that **tags** the watched account, which nobody does by
  accident and which is the only way to exercise the gateway, mention
  detection, the whitelist, the turn window and reply threading without a
  second Discord account. Ticket 37 closed that door because `is_own` could
  not tell the operator typing from this process posting; `Database.we_sent`
  can, and the inbox calls it on every message, so the loop is caught by the
  guard written for it rather than by keeping the door shut. **A DM is not a
  tag**: every message in a one-to-one DM carries `MentionType.DM` whether or
  not anyone was named, so counting it would make every "ok" the operator
  types open a task — the same loop through a different door. The
  task goes to `handled_by_operator` — not `done`, so it is reopenable and
  countable — and everything queued about it is withdrawn.
- **Three prompt families, no shared text — and one prompt module per family.**
  `friday.triage.prompt`, `friday.extraction.prompt`,
  `friday.responder.prompt`. Wording and assembly both live in that family's
  own module — one module that answers "what does this family's prompt look
  like", the contract notes as comments directly above each text; no family
  imports another's (an `ast` test, not a grep: `from x.y import prompt` names
  the module in the alias, and a grep for the dotted name never saw it).

  **There were four**, and the lesson the fourth left is worth more than the
  module was. The graph nodes' prompt lived at friday.dag.api_issue.prompt —
  named here without backticks on purpose, because in this file a backticked
  module is a claim that it exists, and that one does not — and the test
  enumerating families *derived* the module name from the family name, so when
  ticket 15 moved it inside the graph the test silently stopped checking it. The module went with the five-node `api_issue` graph;
  the rule is that a list of families is written out and asserted, never
  derived. This paragraph said "four" for some time after there were three,
  which is the same failure in prose.

  The summariser builds a prompt too (`friday/memory/channel_context.py`) and
  is **not** a fourth family: it has no `prompt` module, because its wording
  is a handful of constants beside the agent that runs it. It is still held to
  the mechanism rules below, and the family-enumerating tests name it
  explicitly for that reason.

  **What is shared is mechanism, and every agent is assembled from it.** The
  seam owns the shape of a section, the escaping at its boundary, and the one
  joiner: `assemble(*sections)`. A prompt module supplies wording and says
  which sections it wants; it never builds a `Section` by hand and never joins
  them itself. Two `ast` tests enforce both halves, because before them four
  modules each had their own `"\n".join(...)` over their own list — so four
  prompts could drift apart in shape while each looked locally reasonable, and
  the summariser had no sections at all, just a bare string feeding a model
  whose output every later prompt for that room reads.

  **A section that describes a tool renders only if the agent has the tool.**
  `clarification_system(None)` and `memory_tool_system(available=False)` are
  not defensive typing; they are the fix for the failure this system has
  already paid for — 79% of the highest-volume prompt here was once
  instructions for writing replies it never writes. An agent told about a door
  that is not in the room goes looking for it.

  **Only the agent that speaks for the operator carries a `soul`** — the
  responder, and nothing else. Not cosmetic: an extractor told to write in
  Vietnamese puts `sản xuất` where `friday/domain/validation.py` wants
  `production`, the value fails its rule, and the reporter is asked to confirm
  what they already said. Tested.

  This said "the two agents" until the second one stopped existing: the graph's
  composing node had its own copy of the voice, and it went with the five-node
  `api_issue` graph. One agent carrying it means the "two copies, deliberately
  divergent" argument that used to sit here is currently moot rather than
  wrong — it applies again the day a second agent writes to a person.

  **`trust_boundary` describes a convention, so an agent claims it only if
  its input actually uses it.** The section says "anything a person sent you
  arrives wrapped like this"; an agent told that, whose input contains no
  markers, has been told about a door that is not in the room — the same
  failure as describing a tool it does not have. It was in five prompts when
  only three wrapped anything. All four prompt-building agents claim it and
  wrap now, and an `ast` test pins claim-and-wrap together.

  Two routes put the markers in, and confusing them escapes text twice.
  `user_input` escapes *and* wraps, which is right for raw text — the
  extractor. `quoted=True` on a section wraps a body the builder already
  escaped, which is right for everyone else; passing an already-escaped
  section to `user_input` showed the model `&amp;lt;b&amp;gt;` where a
  reporter wrote `<b>` and escaped the section's own tag into text with it.

  **Anything this system stores and later reads back into a prompt is stored
  plain.** `channel_derived` escapes once, at the seam, which is what makes a
  hallucinated note data rather than a section — so a value arriving already
  escaped gets escaped twice. The summariser is the one writer that had to be
  taught this, because it is *shown* an escaped transcript and quotes it back.
  A line-oriented section also has to defend its own delimiter: `html.escape`
  leaves newlines alone, so without that a summary could forge a second
  `key:` line in the section describing the room.

  The responder's section order is load-bearing (stable-first is the
  prompt-cache hit) and has its own test.
- **A channel is summarised when the room has said more, and on no other
  condition.** The rebuild used to sit behind `if promoted:` in the heartbeat —
  promotion counts staged observations, nothing has written one since
  `remember` was removed, so the machine-written half of every channel file
  was only ever written by hand, for months, with a green test asserting the
  arrangement was deliberate. It runs every beat now and asks each room
  whether it has said anything since the summary it already has; that mark
  lives in a `state` section *outside* `derived`, because everything in
  `derived` is rendered into that room's prompts and a message id is not
  context.
- **Silence is not approval.** Only a classification the operator marked
  *right* becomes a few-shot example, and only a classifiable type at that. An
  unmarked classification is one nobody read.
- **Agents never write long-term memory directly.** `remember()` writes to a
  staging tier that is never read back into a prompt; a promotion pass moves
  only what an approved outcome corroborates. A test fails if any module but
  the store and the promotion reads it.

## Conventions

- `pytest` + `pytest-asyncio` in the `dev` group, `asyncio_mode = "auto"` so
  async tests need no decorator. `uv run pytest -q` runs the suite.
- No linter or formatter is configured. If you add one, wire it through `uv`
  and record the command here.
- **Structured config in `config.yaml`** (per-agent models and caps, channel
  whitelist, thresholds) — version-controlled, so changes are reviewable diffs.
  `.env` is for secrets only.
- `.gitignore` covers `__pycache__/`, build artifacts, `.venv`, and the runtime
  state that must never be committed: `data/`, `*.db*`, and `.env`.
- The Discord user token is unscoped account access — it must never reach logs,
  tracebacks, or the task DB. `friday/ops/redact.py` enforces this on the way out,
  including from `sys.excepthook` and `threading.excepthook`, which the logging
  filter cannot reach.
- **A rule worth stating is worth a test.** Several of the constraints above
  are enforced by a `grep`-based test rather than by memory, because the ones
  that were only written down are the ones that drifted.

## Verifying a change

A ticket, an edit to logic, a refactor — none of them are done until:

1. **The whole suite passes.** `uv run pytest -q`, not a `-k` subset. Most of
   the constraints above are enforced by a test rather than by memory, so a
   green suite is the only evidence that the rules survived your change.
2. **A subagent has checked the change, not you.** `code-review` for the
   Python — a second read catches what the person who just wrote it stops
   seeing.
3. **Any guard you added has been deleted once and watched go red.** A test
   that still passes without its guard was testing nothing.

Report what the suite actually said. A step you skipped is worth saying out
loud; a failing test reported as passing is the one failure this file cannot
catch.

## Agent skills

### Issue tracker

Local markdown under `.scratch/<feature-slug>/issues/`, one board per feature, each with its own spec beside its issues. `docs/SPEC.md` is the original board's. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical roles, unchanged (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: **`CONTEXT.md`** at the repo root holds the domain vocabulary —
twenty-one terms, from Message and Conversation through Task, Triage,
Extraction, Workflow, Graph, Tool server, Persona, Harness and Observation to
Outbox, Approval, Provider and Sweep. Read it before naming anything, and add
the term there when you name something new. `docs/adr/` does not exist yet.
See `docs/agents/domain.md`.
