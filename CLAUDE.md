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
33. **Nothing on this board is open.** Ticket 19 (Liquid Glass) was retired
in favour of `.scratch/a-monitor-on-the-whole-path/`, a 13-ticket board that
redoes the UI as a real-time operator monitor dashboard (Monitor
front door, SSE updates, drill-down paths, motion + skeleton + toast,
axe-core + Lighthouse gates). Ticket 20 (SSE) landed in ticket 05
of that board. 34–45 are done.

**Ticket 18 is retired too, and this paragraph said the opposite for
months.** It read "ticket 18 stays `ready-for-agent` — the React+Vite
decision still holds; nothing in the new board reverses it". Half true and
wholly misleading: React+Vite did hold, and the monitor board reversed
*three* of that ticket's seven criteria by building the SPA **inside this
repository**. Its own repo, a pinned published artefact, and a page that
offers no way to change anything are all decided the other way — there are
four write routes now and the operator uses them. The ticket carries the
table, checked against the code that landed. This is the drift this file
warns about in its own next paragraph, found by asking what was left to do
rather than by anything breaking. All of them came out of watching real threads rather than
reading code: the reporter replied and nothing could hear the answer, sent the
details in a second message and nothing read it, asked what a correlationId is
and nothing could explain, and the operator answered by hand while the agent
went on asking. Each ticket names what blocks it; work the frontier.

A fourth board, `.scratch/every-answer-has-a-shape/`, holds nine tickets.
**Every ticket but 03 is done.** 04 and 06 were merged, because replacing
`MemoryScope` turned out to be eight production sites reading the same four
fields rather than the wide refactor the board planned for.

**03 is `ready-for-human`**: the operator fills the evaluation rows, which D18
says are theirs by right, since a classifier scored against labels a model
chose measures nothing. **Ticket 07 shipped without its eval reading**, which
is a deliberate exception to the verifying-a-change rule below rather than a
skipped step: D16 puts the baseline first, and running the eval now would
produce an "after" with no "before" against a set too thin to catch a
regression. The runner is ready and reports the new out-of-set number; the
reading is owed the moment the rows exist.

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

The page in `web/` is React + Vite, built to static files that
`friday/ops/api.py` serves from the same process — one container, no Node at
runtime, and `web/dist/` is never committed (board D4: a build artefact in git
can disagree with its source and nobody notices):

```bash
cd web && npm install     # once
cd web && npm run build   # produces web/dist, which the API then serves at /
cd web && npm run dev     # port 5173, proxying /api to 127.0.0.1:8086
uv run serve_board.py     # the API alone against the live db, for `npm run dev`
```

**React's one rule is checked by `tests/test_web_hooks.py`**, and it is there
because this repo has no way to notice otherwise. `FlowScreen` called
`useEffect` below two early returns, so the render before the fetch resolved ran
two hooks and the render after ran three; React throws on the mismatch, the tree
unmounts, and the page renders *nothing* — which from the outside looks exactly
like a backend fault, since the API answers correctly. Every visit to
`/flow/{provider}/{id}` died the moment its data arrived, through a code review
and a green suite, because there are no JavaScript tests here and no linter, so
`eslint-plugin-react-hooks` — which exists for precisely this — never ran. The
guard is narrow (one shape: a hook after an `if` that returns) and is a
stopgap: wiring the real plugin would replace it, not duplicate it.

`web/src/api-types.ts` is **written by hand, not generated**, and that is a
decision rather than an omission: every route is annotated `-> dict` and
builds its body by hand, so `/openapi.json` describes each response as a bare
object — a generated client would pin the *routes* and none of the *fields*,
while the failure worth catching is a renamed field rendering `undefined` in
silence. `tests/test_web_contract.py` is the guard instead. It calls the real
converters, reads the keys they emit, and fails when they stop matching those
types. There are no JavaScript tests, deliberately.

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
| `config.yaml` | Per-agent models and caps, channel whitelist, thresholds, MCP servers, and the sensitive words that keep a message away from the model |
| `friday/config.py` | Loads `config.yaml` and resolves `${VAR}`. Outside the packages because it is read before any of them |
| **`friday/domain/`** | The vocabulary, and nothing else: `models.py` (every dataclass), `conversation.py` (what counts as one exchange), `states.py` (`TaskState`, `OutboundState`, and the legal transitions), `actions.py` (`Ask`/`Reply`/`HandOver`, what a decision about a task comes to), `validation.py` (the rule engine, one call site), `memory_guard.py` (ticket 11: whether a line reads as an instruction at this system's own mechanism rather than a fact, checked at every memory write path) |
| **`friday/store/`** | `schema.py` holds the mapped classes, `db.py` is the only store and converts at the edge — nothing above it knows SQLAlchemy exists |
| **`friday/agent/`** | What it takes to call a model, and nothing about what to call it for: `harness.py` (the only module that may import the SDK), `structured.py` (asking for a shape and checking what comes back against it), `instruction_prompt.py`, `skills.py`, `mcp.py`, `llm_log.py` |
| **`friday/memory/`** | What is kept between tasks, in tiers that never mix: `channel_context.py` (per-channel YAML), `verdicts.py` (the operator marking a classification right). There was a third — `observations.py`/`notes.py`, staged guesses promoted once an approved outcome corroborated them — dropped once it had gone months with no producer (ticket 09's D9); an agent's own memory is a tool now, `friday/tools/memory.py`, not a tier here |
| **`friday/ops/`** | Alive and safe, deciding nothing: `liveness.py`, `redact.py`, `api.py` |
| **`friday/text/`** | `transform.py` splits code out before cleaning the prose; `param_hygiene.py` cleans one value. Decides nothing |
| `friday/inbox/` | Deep module: `stream()`, `sweep_once()`, `tally()`. Gateway, backfill, cursors and dedup are implementation |
| `friday/providers/` | `Provider` protocol; `providers/discord/` holds `user.py` (the account), `bot.py` (approval cards) and `normalise.py`. Its `__init__.py` is empty on purpose |
| `friday/triage/` | Classification and nothing else, its sensitive-word prefilter, and the loop that polls untriaged messages. `context.py` gathers what a mention is shown (D26); `prompt.py` renders it |
| `friday/extraction/` | Everything a task knows, lifted out of what the reporter wrote. One extractor per task type, each owning its prompt and its `Params` schema; **one `extractor` block in `config.yaml` serves all of them** — it was one block per type, and all three held identical values for as long as they existed, so what the split bought was one configuration written three times. `context.py` is node 0's own gather function (ticket 15, D26): the transcript, the room, the domain memories, the outstanding questions and `known`, one call, one frozen `FullContext` |
| `friday/dag/` | `engine.py` is the graph framework — nodes, edges, checkpointed resume — and `state.py` what a run accumulates; the package's `__init__.py` is empty on purpose. `dag/prepare.py` builds the entry node every graph shares and holds the fill-and-validate mechanism it runs. `dag/router.py` maps a task type to a graph. **Every type now gets the same one-node graph** |
| `friday/tasks/` | The pool: pulls pending tasks and hosts their graphs. Stand down, announce, host the graph, act on the outcome — nothing about what a graph decides |
| `friday/tools/` | Every tool an agent may call, one module per subject — reaching a skill (`fetch_skill`, `search_skills`, `describe_skill`, `read_skill_file`), remembering (`memory` — `memory_search`, `memory_add`, `memory_propose`, `memory_update`, `memory_delete`, scoped per channel, wired to the responder; ticket 09's D9, `memory_propose` ticket 12's D19). **Do not trust this sentence's count** — `tests/test_tools.py` writes the list out and asserts it, and that is the one place that cannot be wrong. Four went on board `every-answer-has-a-shape`, and none of them is a capability this system lost: `ask_clarification` had no caller and never had one; `ask_for_fields` became a field of the extractor's own answer; and `classify` and `skip` became the one closed set triage answers, generated from `Decided` by the harness rather than declared here. A test asserts the list, factories built rather than skipped, and forbids declaring one anywhere but here and `agent/harness.py`'s answer tool |
| `friday/responder/` | Drafts a reply in the operator's voice |
| `friday/outbox/` | Nothing is sent by a caller: it is a row, and one loop delivers it |
| `web/` | The operator monitor, on `:8086`. A React + Vite SPA built to static files and served by `ops/api.py`'s app — one process, one container, no Node at runtime. The current shape is the monitor dashboard described in `.scratch/a-monitor-on-the-whole-path/spec.md`: a live feed driven by SSE (`/api/events`), running tasks, drill-down to the Flow screen, breadcrumbs, the Agent vs Reporter marker on Rooms rows. The dark palette and motion tokens live in `web/src/index.css` under `:root` — no component file carries a hex literal or an inline `style={{}}` (`tests/test_web_tokens.py` enforces this). Keyboard shortcuts (`g m`, `g b`, `g r`, `?`, `/`, `r`, `esc`) are wired in `web/src/keyboard.ts`; `?` opens the overlay. Replaced `friday/board/`, 196 lines of f-string HTML and HTMX polling, which was deleted rather than ported: it rendered the same prompts and provider errors as the JSON API while running none of them through `redact.scrub`, which is the two-renderers failure this file already records once for escaping |
| `migrations/` | Alembic revisions |
| `tests/` | Driven through two seams: a fake `Provider` and a scripted model transport |
| `docs/` | `DESIGN.md`, `SPEC.md`, `agents/` |
| `.scratch/` | Local issue tracker |
| `evals/` | The classifier's regression net (ticket 06): `triage.jsonl`, frozen; `build_triage_set.py` refreshes it from live data, by hand; `run_triage_eval.py` scores the live classifier against it and is not run by the suite — it calls the configured provider. `evals/README.md` says what a run costs |

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
with the function.

So **every tool lives in `friday/tools/`**, one module per subject, and
`tests/test_tools.py` enforces both halves of that: the list of tools is
asserted rather than described, and no tool may be declared anywhere else.
Both spellings are checked by reading the syntax, since grep sees only one of
them. A tool that needs something injected — `fetch_skill` a skill library —
stays a factory; that is a different thing from living somewhere else.

**One exemption, and it is one file rather than a rule.** The harness builds
the tool an agent with a declared shape answers through. It cannot live in the
package — `FunctionTool` comes from the SDK, which only that module may import
— and it is not a capability, since answering is not a door an agent chooses
among. Both guards skip that path, matched as a whole path rather than by
basename: the basename form exempted *any* file called `harness.py` anywhere
under `friday/`, which is a hole nobody meant to open and nobody would have
noticed. `test_the_one_tool_outside_the_package_is_the_answer_tool` asserts
what lives there, so the exemption cannot grow a second occupant.

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

**An agent whose answer is a tool call stops on the answer, not on the first
tool output.** `stop_on_first_tool` looks right and is not: it ends the run at
the first tool's *output*, and a failure string is a tool output the SDK
cannot tell from a success. So the "try again with valid JSON" the exemption
above exists to deliver became the run's final answer, and the one party who
could act on it never saw it — a mention the model had all but classified
became work for a person. An agent built with `answers=` gets a terminator
that checks for an *instance* of the shape. `harness.stop_when(predicate)` was
the general version, parameterised by a predicate over the run context; it went
when triage — its only caller — stopped needing a predicate at all, rather than
being kept for the caller that would have justified it.

**The correction budget is one turn, and it is `max_turns`.** A bad call
spends a turn, so `max_turns: 1` plus the one `run_structured` adds gives
exactly one retry; a second bad call overruns, the harness turns that into a
`last_error`, and the mention lands where every other triage failure lands. A
model that cannot get its own schema right twice will not on the third go, and
this is the highest-volume path in the system.

**That turn is added by the harness, not asked for by callers**, and the
reason is arithmetic nobody should have to redo. A run whose answer fits ends
on its *first* turn — the terminator finishes it the moment the tool returns
an instance — so the call and its result are not two turns here. Triage and
the extractor each passed `extra_turns=1` on top of the one `run_structured`
adds, which bought a second correction nobody decided on; both now pass
nothing.

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

  **One outcome answers nothing, and it is still not a discard.** A turn
  older than `max_message_age` is recorded as `outdated`, opens no task and
  reaches no model. What the rule above forbids is a mention that leaves no
  trace; this one leaves a row, a reason and a place on the board, and only
  the reply is skipped. It is the prefilter's shape with the opposite
  conclusion — that one **holds** a message *for a person*, because somebody
  still needs to see it; this one decides nobody does. Judged by the turn's
  newest message, so a live follow-up pulls its older part in, and never
  applied to a reply answering something this system asked. Unset by default,
  for the reason `daily_token_budget` is — **and set to `'24h'` in this
  repo's `config.yaml` since 2026-09-15**, which is the date the rule
  started actually running rather than merely existing.
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
- **An agent that answers a shape declares it at construction, answers through
  a tool generated from it, and is believed only after the arguments are
  checked in this process.** `Harness(answers=<dataclass>)` is the
  declaration; `run_structured(prompt)` returns an instance of it or `None`.
  One dataclass is the only source: `friday/agent/structured.py`'s `describe`
  writes the prompt text and the tool's description from it, `_answer_params`
  puts each field's own `doc` on the matching parameter, and `fits` validates
  the arguments with pydantic. No usable answer is `None`, which is not an
  empty result and cannot be mistaken for one.

  **Declared per agent, not per call**, because an answer shape does not vary
  between calls — the summariser always answers a `RoomSummary`, each
  extractor always its own type's `Params` — so the tool, its terminator and
  its `tool_choice` are built once rather than on every call.

  **A tool call rather than a written answer, and that is measured.** Every
  written answer the configured provider sends carries a `<think>` block, a
  ```json fence and prose after it, so `json.loads` on the reply fails every
  time; a tool call's arguments arrive in their own protocol field, and a
  `curl` carrying `{"a":1}` came back byte for byte.

  **A written answer is still read, as the fallback.** Forcing the call is not
  a guarantee — the probe that found the clean arguments also found this model
  answering outside a closed enum it had just been given — so prose that turns
  up anyway goes through `find_json` and the same `fits`.

  **One correction, and it is the turn budget.** A call that does not fit
  comes back as the tool's *own output*, naming the field, so the model fixes
  it inside the same run and `max_turns` decides how many goes it gets. There
  is no second retry loop and **no second run**: a structured call is one
  `timeout_seconds`, which is what makes the one-run bound above true for it
  too. This paragraph said the opposite — "a correction turn is a second run,
  so it is a second `timeout_seconds`" — and that was true of the
  written-answer version it replaced.

  **The failed arguments are not quoted back.** The extractor copies a
  reporter's bytes verbatim, so its own arguments are reporter-controlled
  text; the reason comes from `fits`, in this process, and names the field.
  A test forges a delimiter inside a refused value and checks the reason
  carries none of it, because pydantic keeps the offending value on a
  neighbouring key of the same error object and a release that folded one
  into the other would open this quietly.

  **Three mechanics that are not obvious and decide the design**, each found
  by prototype or by a failing test rather than by reading: the tool body
  **returns** its problem, since a raise inside `on_invoke_tool` is wrapped in
  `UserError` and fails the whole run; "answered" means the tool returned an
  **instance**, since an error string is a tool output too; and the instance
  is read off the run's **items**, since the SDK stringifies a tool-supplied
  final output unless the agent has an `output_type` — and setting one is the
  single thing this design may not do, because that is what emits the
  `response_format` envelope.

  **The answer tool is the one tool outside `friday/tools/`.** It cannot live
  there: `FunctionTool` comes from the SDK and `harness.py` is the only module
  that may import it. It is also not a capability — answering is not a door an
  agent chooses among. `tests/test_tools.py` asserts there is exactly one such
  function in exactly that file, matched by its full path, so the exemption
  cannot grow a second occupant.

  **What it replaced could not fail, and that was the bug.** Two hand-written
  parsers turned whatever a model said into structure by guessing:
  `friday/extraction/`'s brace scan plus a `key: value` line scraper, and
  `channel_context`'s bare `json.loads`. Neither checked a single type. A
  dataclass constructor accepts `environment=["a","b"]` without complaint, so
  a wrong-typed answer built a `Params`, reached `validate`, and surfaced as
  `unhashable type: 'list'` quoted at the operator inside a hand-over. An
  unreadable answer became `{}`, and since every `Params` field has a
  default, **an empty extraction was indistinguishable from a successful
  one** — the reporter was asked for what they had already written. And the
  summariser's `json.loads` raises on every reply the configured provider
  actually sends, so the fallback fired every time: the whole blob, `<think>`
  block included, was stored as what the room was about and rendered into
  every later prompt for that room.

  **Not `response_format: json_schema`, and the reason is measured rather
  than assumed** — see `docs/DESIGN.md`, where the year-old instruction to
  verify this against the provider finally was. MiniMax-M3 accepts that
  parameter and ignores it, answering outside the schema it was just given.
  A provider that rejects it is one you find out about; one that accepts and
  ignores it leaves a schema in the code that reads like a guarantee. The
  SDK's own `output_type` cannot be used either: it emits exactly that
  envelope for Chat Completions with no prompt-only mode, and its validation
  is gated behind the same flag as the wire format. The tool's parameter
  schema *is* sent, because that is how a tool is declared, and nothing
  relies on the provider honouring it. **A shape's own docstring is not
  sent**: pydantic puts it on the object as `description`, and these
  docstrings are developer prose — `RoomSummary`'s runs to nine paragraphs
  about why it has four fields and not six.
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
- **One state travels a message's whole journey, and it is read-only.**
  `FridayState` (`friday/domain/models.py`) is what a run is *about*: the
  room, the agent now running, and — as the journey supplies them — the
  provider, thread, message, author, reply and task. It is what the SDK's
  per-run `context` carries for the memory tools, and the store's memory
  methods take it as the scope, reading the channel, task, agent and source
  message off it.

  **It replaced `MemoryScope`, which is deleted rather than aliased.** That
  named the same room under a second name for a narrower purpose, and a second
  name for one thing is how two things drift. Nothing about who may read a
  memory changed: scope is still runtime-supplied, still never named by the
  model, and a channel's memory is still invisible to a run in another one.

  **Fields cannot be assigned; every change is a named method returning a new
  state** — `as_agent`, `for_task`, `about_message`. Not a style choice: this
  value is handed to a tool, to the store and to the recording sink inside one
  run, and a field anything could assign makes "what can change this, and
  where" unanswerable. There is deliberately **no** general `with_(**fields)`,
  which would make every change legal again and put that list back out of
  reach. Only `channel_id` and `agent` are required — the boundary and the
  provenance; the rest are `None` until the journey supplies them, the way
  `task_id` already meant "this run belongs to no task".

  What it is for is visible at `Pool._say`: the responder took `channel_id`,
  `task_id` and `message_id` as three parameters, and the pool built all three
  from a conversation it was already holding. It takes one object now, which
  is the whole of D22 — adding one more fact must not mean threading one more
  parameter through five signatures.

  **And it is the only thing the SDK's per-run `context` carries.** That slot
  used to mean two things at once: "who is this run about" for the responder's
  memory tools, and "where the answer will appear" for triage and the
  extractor, whose tools wrote a result into an object the caller read back
  afterwards. So a classification was not the return value of anything, and
  following "what did triage decide" meant knowing that a tool wrote into a
  capture, that a predicate watched it, and that the runner read it after the
  call returned — none of it in any signature.

  Every agent that declares a context type declares `FridayState`, including
  one with no memory tools: the recording sink reads the message and the task
  off the same object, so a context type that appeared only when the tools did
  was the last place the slot's meaning depended on how the agent was built.
  `tests/test_run_context.py` holds both halves — that nothing assigns through
  a `.context`, and that every `context_type=` this project owns is that bare
  name (it scanned `friday/` and `tests/` alone until a review pointed out
  that `run_agent.py`, the one place adapters are constructed, was outside it).
  **The second is the guarantee and the first is a net.** `FridayState` is
  frozen and every one of its fields holds something that cannot be changed in
  place — asserted, since frozen alone says nothing about a `list` field — so
  an agent built that way *cannot* be written into by any spelling. The scan
  covers the other door: `context_type` is optional, so an agent can still be
  handed an object that is not the state at all.

  There is deliberately **no third guard on the word "Capture"**. One was
  written and deleted: it fired on two `Model` subclasses in tests that capture
  a *prompt* — the word is not the pattern, and a guard that fires on innocent
  code teaches a reader to edit the guard. What the two above catch is the
  shape the side channel actually took here, not every shape it could take: an
  agent that declares no context type, is handed a capture through
  `run(context=...)`, and is mutated by a method call rather than an
  assignment would pass both. The closure is the construction, not the scan.

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
  template names something untranslatable* — `correlationId`, `curl`. Five of
  the eight questions this system asks name nothing of the kind ("what access
  you need", "which document you mean"), and for those there is no way to tell
  a faithful Vietnamese rewording from a different question. The other four
  rules carry those. A test says this out loud, so the paragraph cannot
  quietly become a stronger promise than the code makes. Blunt on purpose: a
  false refusal sends a plainer question, a false acceptance sends the
  operator's colleagues something the operator did not say.

  **This paragraph said "four of the seven" until ticket 13, and it was wrong
  when it was written.** `project` was asked too — through a fallback that
  turned its field name into a sentence — so this system asked eight questions
  while this file said seven, and nothing noticed, because "the project" reads
  well enough that a missing phrase looks like a written one. The count is the
  documentation drifting from the code in the file that warns about exactly
  that, which is the reason it is now asserted rather than only stated:
  `test_which_questions_this_rule_binds_is_derived_not_counted` applies
  `friday/responder/check.py`'s own pattern to the phrases and pins the total.

  **How to ask about a field lives on the field**, in `ask` metadata beside the
  `doc` that tells the extractor what the field means — one string per reader,
  both where the field is, which is the arrangement `doc`'s own comment exists
  to explain the need for. A cross-field rule carries its own phrase, because
  its subject is a sentinel and not a field; `OneOf` holds that argument and
  the reversal behind it, and is the only place it is written out. Resolving a
  phrase is `friday/domain/validation.py`'s `asked_as`, beside the engine whose
  `_RULES` it reads — a resolver in `friday/dag/` made a second module walk
  that dict. There is no fallback: a subject with no phrase raises, which the
  author sees as a red test and the operator would see as a hand-over carrying
  the reason.
- **Nothing is sent by the caller that decided to send it.** An outbound
  message is a row; one loop delivers it. Approval is enforced as a predicate
  in the query that selects sendable rows, not as a check each caller must
  remember — see `_NEEDS_APPROVAL` in `friday/store/db.py`.
- **SSE events come from the store rows the system already writes.**
  `Database.record_model_call` and `record_tool_call` publish on the in-process
  `EventBus` after the row is committed; the `/api/events` SSE endpoint
  replays from the bus on reconnect (`Last-Event-ID`). One writer, one queue;
  the monitor screen reads live. Out-of-band events (task state changes, future
  outbound sends) extend the bus the same way.
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
- **Triage classifies and nothing else, and it answers one closed set.** No
  parameters, no summary — a `Decided`: a member of `models.DECISIONS`, which
  is every task type plus `skip`, and a confidence. It arrives through the
  tool `Harness(answers=Decided)` generates, and the arguments are validated
  in this process before anything acts on them. `Decided` *is* the shape, so
  there is no second declaration of what triage may say.

  **This reverses the `classify`/`skip` split**, whose recorded argument was
  that everything `classify` names opens work while `skip` names the absence
  of it. That is true, and it is `TriageRunner._apply`'s business, where it
  stays. What the split actually bought was two validations of one question:
  an invented type could reach `_apply` and open a task the pool then
  discovers has no graph, and "there is no work here" was checked less
  strictly than "there is". Measured, the wire closes nothing — asked for that
  exact enum, the configured provider answered `hardware_issue`.

  **An invented type and an outage are different failures, counted
  separately.** `NeedsHuman.out_of_set` says which, `Harness.unfit` is where
  it comes from, and `evals/run_triage_eval.py` prints the count beside the
  accuracy — always, including as zero, since a line that appears only when
  it is non-zero is a line whose absence means both "none" and "not
  measured". One says a prompt or a model is wrong; the other says the
  network was.

  The signal is raised by the answer tool's own body rather than by the run,
  because the run may never get back to raise it: a model that answers wrongly
  twice overruns `max_turns` and `run_structured` returns `None` having seen
  no reply at all. Arguments that are not a JSON object do **not** raise it —
  that is the model saying nothing, not naming something.

  It was `create_task`, and it creates nothing — it records a `Decided`; the
  task is opened by `TriageRunner._apply` and only sometimes. A tool name is
  an instruction to the model, so a model told to "create a task" believed it
  was doing something it was not.

  Everything a task knows is lifted out of the message by `friday/extraction/`,
  one extractor per task type, reading every message linked to the task. The
  tool schema is the enforcement: a tool parameter is an instruction to the
  model, so `correlation_id` in the schema *is* triage extracting whatever the
  prompt says, and a test pins that nothing triage was handed asks for
  anything but a type and a `confidence`. Each type's description in the enum
  is read from its own `Params` class's docstring, so a fourth type is a
  fourth class and not a second description of it. **A class that never wrote
  one is refused at import** — and the check is not "is it empty", because a
  `@dataclass` always has a docstring: absent its own, Python synthesises the
  constructor signature, and `ApiIssueParams(summary: str = '', ...)` reads
  like a description to everything except a person.
- **A classifiable task type without a configured extractor is broken**, not
  degraded: it opens tasks with no parameters and asks the reporter for what
  they already said. `friday/extraction/`'s `EXTRACTS` and `PARAMS` must
  agree, and a test says so.
- **The unit is a turn, not a message.** A mention opens a turn — everything
  the same person goes on to say — and triage reads it once, when they have
  been quiet for `turn_seconds` and are not typing, or somebody else spoke.
  Turns are computed when read, never stored: when a message arrives it is not
  yet known whether the turn is over.
- **Triage is shown the turn and the room's summary, not an unbounded
  transcript** (ticket 09, reversing ticket 26). It used to receive every
  message that had ever mentioned the operator in this conversation — no
  limit, growing forever — chosen deliberately for prompt-prefix stability
  and pinned by three tests. What that bought was real: an unbounded, never-
  evicted window is stable at the front by construction. What it cost was a
  hallucinated colleague, read out of a message *body* in a 24-line transcript
  because nothing told the model who was speaking, and 2,552 characters of
  transcript on the highest-volume path in the system to buy a guarantee a
  much smaller mechanism gives for free.

  The replacement is `friday/memory/channel_context.py`'s own structured
  summary (ticket 06), read through `channel_derived` — the same section the
  responder already reads — plus the turn itself, rendered as the messages it
  actually is rather than pre-joined into one string. The three tests that
  pinned the old guarantee stay exactly as they were: `db.relevant_messages`
  is unchanged and still serves the responder (`Pool` reads it for the same
  reason triage no longer does), so the property it proves is still real for
  that caller. Three new tests prove the same *shape* of guarantee — a stable
  prefix, measured, not assumed — for triage's new mechanism instead: the
  summary section does not move as the room says more between rebuilds, two
  different turns against the same summary share everything but the turn,
  and the measured shared prefix between two calls — instructions and the
  per-call input concatenated, which is a conservative proxy: the real wire
  format carries more around them, identical between the two calls compared,
  so the true ratio a provider sees is at least this — is over 90%.

  Domain memory, task parameters and artifacts do not reach triage under this
  design, on the same ground as before: it decides a label, not a value, and
  those three are exactly what a value gets built from.

  **Gathered by one function, not resolved inline** (ticket 14, D26).
  `Triage.decide` used to look the room up itself and hand `build_input` the
  turn and the room as two separate arguments — the only point at which
  "what did the classifier see for this mention" existed was the call to
  `build_input` itself, nowhere a value could be logged or inspected first.
  `friday/triage/context.py`'s `build_light_context` is now the one place
  triage resolves a room; it returns a frozen `LightContext(turn, room)`,
  and `build_input` takes that value and nothing else. The turn itself is
  still computed by `TriageRunner.turn_from`, not by the builder — the
  runner needs it for its own "has this turn closed" question, and a second
  computation here would be a second place deciding what a turn is. The
  builder reads and logs one debug line of counts and sizes; it never
  writes, never imports a section builder, and never renders — the same
  rule an extraction gather module will be held to, once ticket 15 builds one.
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
- **Verbatim material is stored whole and pointed at, never paraphrased**
  (`.scratch/what-the-room-already-knows/`, ticket 07, D8). Code, a stack
  trace, a `curl` — anything `friday/text/transform.py`'s `transform` splits
  out of a message's prose — becomes its own `Artifact` row, `content`
  untouched. What a build gets instead depends on which build: the
  summariser reads `Database.relevant_messages_in_channel`, which
  substitutes `messages.redacted_text` for `text` — each span replaced by
  `[artifact id: description]`, computed once at record time by
  `friday.text.transform.redact`. Node 0's own read,
  `Database.original_text_for`, was untouched by this ticket and still
  returns a task's material whole; so is every other reader of `text` —
  triage, the responder's tone examples, the Rooms screen. (Ticket 08 later
  gave `original_text_for` a budget of its own — see below — but that trims
  by dropping whole messages, never by paraphrasing or truncating one, so
  every message it does keep is still exactly what was typed.) **The
  description is built
  from shape and size, never from the content's own bytes** — kind
  (curl / stack trace / SQL / code) plus line and character counts. A `curl`
  is usually one line, and "the first N characters" of a one-line artifact
  *is* the artifact, which is exactly the leak this rule exists to close
  since the description is what the summariser is shown; found by mutation,
  not written correctly the first time. `redact` re-splits already-restored
  text rather than the original message, which a code review found no
  proof always agrees with the first split (though a targeted and a
  20,000-trial random search found no disagreement either) — a message
  where it does not is recorded with no artifact and no redaction rather
  than left half-written or crashing the ingest path, the same fallback a
  message with no code at all already has.
- **Agents write their own long-term memory now, and read it back** (ticket
  09's D9, reversing what this file said until 2026-09-06: *"Agents never
  write long-term memory directly. `remember()` writes to a staging tier that
  is never read back into a prompt; a promotion pass moves only what an
  approved outcome corroborates."* That tier had no drift floor problem — an
  agent never saw its own unreviewed guesses — but no producer either:
  nothing wrote an observation once `remember` left the tool list, so
  promotion ran every heartbeat over an empty table for months before both
  tables were dropped.

  What replaced it trades the approval floor for three narrower guarantees,
  all in `friday/tools/memory.py`: a memory reaches a model **only as a tool
  result**, never appended to `instructions` — closed by construction, since a
  tool result cannot rewrite the prompt of every later call the way a promoted
  note once could (commit f0686f2); **scope is runtime-supplied** on
  `FridayState`, read off the run's context rather than named by the model, so
  a channel's memory is invisible to a run in another one; and **ids are
  opaque and sparse**, so an invented one fails rather than landing on a
  neighbouring row. Drift is possible and is bounded by the channel scope and
  the operator's visibility into what was written, not by a corroboration
  count.

  **Wired to the responder, never to triage.** The responder is the agent
  that writes text a person reads, and a room's habits are exactly the kind
  of thing worth remembering; triage stops when its answer tool has returned
  an actual `Decided` rather than on the first tool output, which is the same
  rule that means a `fetch_skill` does not terminate the run before the model
  classifies.
  Whether the summariser should get memory tools is still undecided; the
  extractor's own case is answered below, and by injection rather than a
  tool.

  **A memory carries a `kind`, and the reader follows from it, not from a
  second field** (`.scratch/what-the-room-already-knows/`, ticket 10). Five
  values — `fact`, `constraint`, `finding`, `decision`, `voice` — and
  `reader_for(kind)` is the one function that decides who reads a row, so
  nothing has to keep a second column in agreement with it. The responder's
  memory tools write and search only `voice` (ticket 12's `memory_propose`
  included); the extractor has no memory tools of its own — the same
  reasoning that keeps it off
  triage does not apply here, but its turn budget is one call plus one retry
  and a tool-result round trip would spend it on searching — so the four
  domain kinds reach it by **injection**, through the memory section's
  existing channel slot, alongside the room facts the operator wrote by
  hand. A memory also carries a `status` and a `superseded_by`: correcting
  its wording (`memory_update`) leaves it in place, replacing what it claims
  (`memory_supersede`, new) marks it superseded and points at what replaced
  it, and every reader that serves a model reads active rows only — a
  superseded or deleted one stays visible to the operator and nowhere else.

  **A line shaped like a directive at this system's own mechanism is refused,
  in code, before it becomes a row** (`.scratch/what-the-room-already-knows/`,
  ticket 11, D19, D25). A memory is read back as a statement of fact by a run
  that has none of the context that produced it, so "send without approval",
  "always reply in English", "skip the validation" are instructions with a
  long life and no author present — the most dangerous row this board can
  create. `friday/domain/memory_guard.py`'s `check_not_instruction_shaped` is
  deterministic and involves no model, and it sits at the single write path
  every producer shares: `Database.memory_add`/`memory_update`/
  `memory_supersede`, and `ContextStore.set_overrides`/`init_channel` — the
  route the operator's own hand writes room facts through, which is the
  producer D19 says this board may not ship without. A refused write raises
  `InstructionShaped`; the tool layer and the API route each catch it and
  tell whoever attempted it why, rather than letting it fall through to a
  generic "that tool is unavailable" that explains nothing.

  **Narrower than "any imperative sentence".** `never deploy on fridays` is a
  domain constraint about the team's own practice — exactly the shape
  `MemoryKind.CONSTRAINT` exists to hold — and it is accepted: the check only
  refuses a line that both *reads* like a command (a bare-verb or
  `always`/`never`/`don't`-led sentence-initial word) *and* names one of this
  system's own moving parts (approval, validation, reply, escalation, and the
  like). Either alone is not enough, which is what keeps a real domain
  constraint on the accepted side while still catching a directive aimed at
  the agent.

  **A candidate memory waits for the operator's mark, and the mark is the
  one that already confirms a classification**
  (`.scratch/what-the-room-already-knows/`, ticket 12, D19, D20). D19's
  second producer, beside `memory_add`'s automatic write: the responder's
  fifth tool, `memory_propose`, stages a `MemoryCandidate` in its own table
  rather than writing to `memories` — every reader that serves a model
  would otherwise have to remember to filter a `PENDING` row out, which is
  exactly the guarantee D20 asks to hold structurally instead.
  `candidates_for_channel` (`GET /api/channels/{id}/candidates`) is the
  "place for a person to look" the old staging-and-promotion tier never had.

  **Resolution rides the same reaction the operator already uses to mark a
  classification right or wrong** — `source_message_id` on a candidate is
  the task's own opening message (`Database.source_message_of`), the exact
  one `Verdict` is keyed on, so there is one gesture to learn, not two.
  `run_agent.py`'s `marked()` callback calls `resolve_candidates_for_
  message` right alongside `record_verdict`, on the reaction being *added*
  only — taking a mark back does not un-resolve a candidate it already
  settled. Marked right, the candidate is written through `memory_add` —
  ticket 11's refusal and the 200-per-channel cap (D18) both still apply,
  and either one refusing leaves the candidate `ACCEPTED` with `memory_id`
  still `None` rather than raising into a live reaction handler. Marked
  wrong, it is discarded but stays listed, never deleted. A verdict that
  already exists when a candidate is proposed resolves it immediately,
  rather than leaving it waiting on a reaction that already happened.

  **D18's other half: a full channel is now visible to the one party who
  can clear it.** `Database.full_memory_channels` names every channel at
  `MEMORY_PER_CHANNEL`; the heartbeat says so in its own line and
  `GET /api/board` carries the same list — the ceiling itself is unchanged,
  refusing the write and evicting nothing, but before this ticket the only
  party ever told was the model reading the refusal message.
- **Node 0's own build respects a budget, and the budget is primary; a
  message count is secondary** (`.scratch/what-the-room-already-knows/`,
  ticket 08, D5-D7). `config.yaml`'s `context.extraction_budget_tokens` is
  an *estimate* — characters divided by four, since the configured provider
  has no tokenizer — and unset means no compaction at all, the same
  doctrine `daily_token_budget` follows. `Database.original_text_for` still
  caps at `limit` messages first (unchanged), then, over budget, drops the
  *oldest* of those, never the newest — a reporter's answer to a question
  just asked is always the newest message and the one a follow-up pass
  cannot afford to lose. A bad configured value (zero, negative) raises at
  load, not at run time: the failure shape that emptied five context
  mechanisms in this repo was a value tolerated silently until it mattered.

  **Compaction here drops whole messages; it does not summarise them.** D8
  also asks for a model to compact prose that does not fit a budget — this
  ticket's own scope call, made explicit rather than assumed, is that node
  0's transcript compacts by truncation alone: a model call here would cost
  real money on every task that exceeds its budget, for a mechanism this
  board otherwise keeps free of rolling, per-pass summarisation on purpose
  (D24's concern, arrived at from this side too — a model call that reruns
  on every pass rewrites the prompt prefix and breaks the provider's
  cache). A single message larger than the budget is therefore something
  truncation cannot fix by definition, not a bug in it.

  **Two such passes and node 0 stops trying, visibly.**
  `Database.compaction_on_cooldown` — backed by a small `compaction_state`
  table, `ineffective_count` per task — answers `True` once truncating has
  failed to bring a task's build under budget twice; a warning names the
  task each time, and a task on cooldown is read exactly as if no budget
  were configured, rather than repeating a check that cannot succeed. A log
  line is not the operator's own view of it, so `/api/tasks/{id}/compaction`
  is the second half — code review's own finding, not the ticket's original
  scope.

  **A field already in the task's own parameters drops out of the schema
  the extractor is shown**, the other half of D8's three-way split (a
  schema field is "compacted into the task's parameters" because the
  extractor already copied it there). `known` reaches `build_input`, which
  skips a field once `known` has anything truthy for it — empty string does
  not count, the same rule `_fill` already applies. This is a fifth input to
  `input_fingerprint`: a field getting filled shrinks the schema, which is a
  real prompt change the digest has to see move, corrected in the same
  ticket in `ExtractionMark`'s own docstring, which had claimed the
  opposite.

  **The five signatures that threaded `known` from node 0 to `build_input`
  are gone** (ticket 15, D26): `friday/extraction/context.py`'s
  `build_full_context` is now the one place node 0 gathers everything an
  extraction needs — the transcript under its budget, `known`, and the
  three inputs ticket 01 and ticket 10 added to the extractor's own
  `would_ask` (the room, the domain memories, the outstanding questions) —
  into one frozen `FullContext`. **The room is resolved here now, not by
  the extractor**: `context_store` reaches `build_full_context` by closure
  through `prepare_node`, the same way `budget_tokens` already did, and
  `Extractor` no longer holds a context store or a database of its own —
  `would_ask`, `run`, `extract` and `input_fingerprint` all take the one
  `FullContext` object and nothing else. Fixed as a side effect: `register_
  dags` had been writing the context store into `DAG_DEPS_EXTRA["context_
  store"]`, a dict read by task *type*, so that value was never once
  reachable — nothing had ever read it.

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
4. **A change to `friday/triage/prompt.py`, or to anything upstream of it,
   also needs `uv run python -m evals.run_triage_eval` run against
   `evals/triage.jsonl`, with the accuracy, confusion matrix and threshold
   table reported alongside the change.** The suite's scripted transport
   pins wiring and says nothing about whether the classifier is right; this
   is the only thing that does. See `evals/README.md`.

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
26 terms as of this file's own count (`grep -c "^## " CONTEXT.md`, not
retyped by hand here for that reason), from Message and Conversation through
Task, Triage, Extraction, Graph, Tool server, Harness, Friday state and Memory
to Outbox, Approval, Provider and Sweep. Read it before naming anything, and
add the term there when you name something new. `docs/adr/` does not exist
yet. See `docs/agents/domain.md`.

This sentence has drifted from the file it describes before, silently, which
is the reason to prefer a command over a number the next time this goes
stale: "Workflow" and "Persona" were named here as terms with no matching
heading before this edit and are left that way — pre-existing and not this
ticket's to chase — while "Observation" is the one this ticket's own change
made wrong, since the term is `Memory` now. The count moved to 26 on board
`every-answer-has-a-shape`, which added **Friday state** — and which is a
reminder that the number above is the part of this paragraph to re-run rather
than to read.
