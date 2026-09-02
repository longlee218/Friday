# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status

Running. It ingests Discord mentions, classifies them, opens tasks, asks for
missing details, and sends approved replies as the watched account. Roughly
5,000 lines under `friday/`, on a single branch (`main`), with a passing suite.

The design is settled and written down — see **`docs/DESIGN.md`**, the source of
truth for what this is meant to become. Read it before adding anything
non-trivial. Decisions recorded there were reached deliberately; if you think
one is wrong, raise it rather than quietly building something else.

Work is broken into tickets under `.scratch/discord-mention-triage/issues/`,
derived from `docs/SPEC.md`. Tickets 01–17 and 23–27 and 29–33 are done; 07
was superseded and reopened as 28, and 28 is now retired in favour of 32 and
33. **Open: 18–20 only** — the board's own repo and its UI, deferred by choice.
34–41 are done. All of them came out of watching real threads rather than
reading code: the reporter replied and nothing could hear the answer, sent the
details in a second message and nothing read it, asked what a correlationId is
and nothing could explain, and the operator answered by hand while the agent
went on asking. Each ticket names what blocks it; work the frontier.

This line goes stale faster than anything else in this file. Check it against
the `**Status:**` line in each ticket before trusting it.

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
| `config.yaml` | Per-agent models and caps, channel whitelist, thresholds, MCP servers, each agent's persona mode, and the sensitive words that keep a message away from the model |
| `PERSONA.md` | Who every agent is, before it is told its job. Prose, read once at startup, prepended to each agent's instructions |
| `prompts/` | Every agent's instructions, one file per prompt — the wording is editable prose, the JSON keys and section names inside are contracts with parsers. `README.md` there is the one file never sent to a model |
| `friday/config.py` | Loads `config.yaml`, resolves `${VAR}`, stamps each `AgentConfig` with its persona. Outside the packages because it is read before any of them |
| **`friday/domain/`** | The vocabulary, and nothing else: `models.py` (every dataclass), `conversation.py` (what counts as one exchange), `tasks.py` (`TaskState` and its legal transitions), `validation.py` (the rule engine, one call site) |
| **`friday/store/`** | `schema.py` holds the mapped classes, `db.py` is the only store and converts at the edge — nothing above it knows SQLAlchemy exists |
| **`friday/agent/`** | What it takes to call a model, and nothing about what to call it for: `harness.py` (the only module that may import the SDK), `instruction_prompt.py`, `persona.py`, `skills.py`, `mcp.py`, `llm_log.py` |
| **`friday/memory/`** | What is kept between tasks, in tiers that never mix: `observations.py` (staged), `notes.py` (promoted, and only by an approved outcome), `channel_context.py` (per-channel YAML), `verdicts.py` (the operator marking a classification right) |
| **`friday/ops/`** | Alive and safe, deciding nothing: `liveness.py`, `redact.py`, `api.py` |
| **`friday/text/`** | `transform.py` splits code out before cleaning the prose; `param_hygiene.py` cleans one value. Decides nothing |
| `friday/inbox/` | Deep module: `stream()`, `sweep_once()`, `tally()`. Gateway, backfill, cursors and dedup are implementation |
| `friday/providers/` | `Provider` protocol; `providers/discord/` holds `user.py` (the account), `bot.py` (approval cards) and `normalise.py`. Its `__init__.py` is empty on purpose |
| `friday/triage/` | Classification and nothing else, its sensitive-word prefilter, and the loop that polls untriaged messages |
| `friday/extraction/` | Everything a task knows, lifted out of what the reporter wrote. One extractor per task type, each owning its prompt, schema and model |
| `friday/workflows/` | The simple path — fill in, check, ask, park — the `Ask`/`Reply`/`Park` actions, and the loop that acts on tasks |
| `friday/dag/` | The graph framework — nodes, edges, checkpointed resume, `PauseForHuman`, and the edge router. `dag/api_issue.py` is the first graph; `dag/workflows.py` is what the composition root calls |
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

There is no `nodes/`, `procedures/`, `tools/`, `permissions/` or `hooks/`
package, and their absence is a decision rather than an omission. (`memory/`
was on that list until it existed — the six packages above were carved out of
twenty-two loose modules once flat stopped scaling, which is the same rule
applied at a later size, not a reversal of it.) The
node vocabulary was removed from the design deliberately (workflows are
deterministic Python; only triage and the responder call a model), tools live
beside the state they touch because that is the only place their guard can be
enforced, and the four guards in this codebase answer four different questions
about four different subjects — collapsing them into one package would cost
locality and buy nothing. **Build one of these when a second caller appears,
not before.**

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
- **`friday/agent/harness.py` is the only module that may import `agents`.** The SDK
  is here for speed, not for keeps, and that is only true while replacing it
  means rewriting one file. What other modules need — `tool`, `ToolContext`,
  `Hooks`, the MCP server types — is re-exported from there under names that do
  not mention the library. `tests/test_harness.py` fails if a second module
  reaches past it.
- **Workflows are deterministic Python, and an agent is a node inside one.**
  The route is classify → edge router → graph: which task type has a graph is
  `friday/dag/workflows.py`'s business, not the composition root's. The graph's
  *shape* is code — a model never chooses the next step. Durable resume is
  built (ticket 32): a graph checkpoints after every node, and discards its
  state when the task's parameters change, because a conclusion drawn without
  the correlationId is not a conclusion about the request that has one.
- **A task type without a graph is not a mistake.** It takes the deterministic
  path — validate, ask for what is missing, park — which is all most types
  need. Build a graph when there are steps worth skipping, not before.
- **Nothing is sent by the caller that decided to send it.** An outbound
  message is a row; one loop delivers it. Approval is enforced as a predicate
  in the query that selects sendable rows, not as a check each caller must
  remember — see `_NEEDS_APPROVAL` in `friday/store/db.py`.
- **One persona, three modes, and the mode is per agent.** `PERSONA.md` says
  who the agents are and that people read Vietnamese; `config.yaml` says how
  much of it each agent takes. `full` for the two agents whose output a person
  reads, `language` for the ones filling in structured fields, `none` for the
  ones returning a path or a diff. This is not caution: triage fills in
  `environment` by tool call and `friday/domain/validation.py` requires
  `production` / `staging` / `dev`, so an agent carrying the voice writes
  `sản xuất` and the reporter is asked to confirm what they already said. It
  goes in `instructions`, never the per-call bundle — shared bytes at the
  front of a prompt are the ones a provider's cache reuses across agents.
- **Triage classifies and nothing else.** No parameters, no summary — a type
  and a confidence. Everything a task knows is lifted out of the message by
  `friday/extraction/`, one extractor per task type, reading every message
  linked to the task. The tool schema is the enforcement: a tool parameter is
  an instruction to the model, so `correlation_id` in the schema *is* triage
  extracting whatever the prompt says, and a test pins that every triage tool
  asks for nothing but `confidence`.
- **A classifiable task type without a configured extractor is broken**, not
  degraded: it opens tasks with no parameters and asks the reporter for what
  they already said. `friday/extraction/`'s `EXTRACTS` and `PARAMS` must
  agree, and a test says so.
- **The unit is a turn, not a message.** A mention opens a turn — everything
  the same person goes on to say — and triage reads it once, when they have
  been quiet for `turn_seconds` and are not typing, or somebody else spoke.
  Turns are computed when read, never stored: when a message arrives it is not
  yet known whether the turn is over.
- **The operator's own message ends the work.** Their messages never create a
  task and are always kept, because them answering is what closes one. The
  task goes to `handled_by_operator` — not `done`, so it is reopenable and
  countable — and everything queued about it is withdrawn.
- **Four prompt families, no shared text.** Triage gets the messages; an
  extractor gets the schema and the messages; a graph node gets its own
  instructions and the Node persona; the responder and the composing node get
  the Responder persona, the room's context, examples, conversation and task.
  Escaping is the one shared piece, in one module, and a test says so.
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

## Agent skills

### Issue tracker

Local markdown under `.scratch/<feature-slug>/issues/`; the spec lives at `docs/SPEC.md`. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical roles, unchanged (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: **`CONTEXT.md`** at the repo root holds the domain vocabulary —
Message, Conversation, Task, Triage, Workflow, Harness, Outbound intent, Outbox,
Approval, Sender, Provider, Sweep. Read it before naming anything. `docs/adr/`
does not exist yet. See `docs/agents/domain.md`.
