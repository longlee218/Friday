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
33. **Open: 18–20 only** — the board's own repo and its UI, deferred by
choice. Each ticket names what blocks it; work the frontier.

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

Persistence is **SQLAlchemy 2.0 async** (`friday/schema.py` holds the mapped
classes, `friday/db.py` converts them to and from the domain dataclasses) with
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

**Always autogenerate against a freshly migrated throwaway database, never the
live one.** `data/friday.db` was built by hand-written DDL before Alembic existed
and then stamped, so reflecting it yields ~27 cosmetic differences — `TEXT` vs
`VARCHAR`, `server_default`s the models do not declare, a different column order.
All are functionally identical in SQLite, and all of them would land in a
migration that changes nothing.

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
| `run_agent.py` | Composition root — the only place adapters are constructed, and the only place the asyncio tasks are started |
| `init_channel.py` | One-off: create a channel's context file for the operator to fill in |
| `config.yaml` | Per-agent models and caps, channel whitelist, thresholds, MCP servers, and each agent's persona mode |
| `PERSONA.md` | Who every agent is, before it is told its job. Prose, read once at startup, prepended to each agent's instructions |
| `friday/config.py` | Loads `config.yaml`, resolves `${VAR}` from the environment, stamps each `AgentConfig` with its persona |
| `friday/persona.py` | Reads `PERSONA.md` and assembles the part each agent asked for |
| `friday/models.py` | Every domain dataclass. No persistence, no SDK |
| `friday/schema.py`, `db.py` | Mapped classes; the only store. `db.py` converts at the edge, so nothing above it knows SQLAlchemy exists |
| `friday/conversation.py` | `ConversationId` — what counts as one exchange, and why a thread is its own |
| `friday/inbox/` | Deep module: `stream()`, `sweep_once()`, `tally()`. Gateway, backfill, cursors and dedup are implementation |
| `friday/providers/` | `Provider` protocol; `providers/discord/` holds `user.py` (the account), `bot.py` (approval cards) and `normalise.py`. Its `__init__.py` is empty on purpose |
| `friday/harness.py` | The only module that imports the agent SDK. Builds an agent, runs it, turns failure into work |
| `friday/triage/` | Classification, its prefilter, parameter hygiene, and the loop that polls untriaged messages |
| `friday/responder/` | Drafts a reply in the operator's voice |
| `friday/workflows/` | The simple path — validate, ask, park — the `Ask`/`Reply`/`Park` actions, and the loop that acts on tasks |
| `friday/dag/` | The graph framework — nodes, edges, checkpointed resume, `PauseForHuman`, and the edge router. `dag/api_issue.py` is the first graph; `dag/workflows.py` is what the composition root calls |
| `friday/extraction.py` | Per-workflow field extraction: each workflow owns its prompt, schema and model |
| `friday/skills.py` | Markdown skills the operator writes, offered to reasoning agents by catalogue and fetched on demand |
| `friday/verdicts.py` | The operator marking a classification right or wrong, with a Discord reaction |
| `friday/tasks.py` | `TaskState` and the legal transitions between them |
| `friday/outbox/` | Nothing is sent by a caller: it is a row, and one loop delivers it |
| `friday/observations.py`, `notes.py` | Staging tier, and the promotion that only an approved outcome earns |
| `friday/channel_context.py` | Per-channel YAML: what the machine learned, and what the operator wrote |
| `friday/mcp.py` | Tool servers outside this process, built from configuration |
| `friday/llm_log.py` | Both sides of every model call |
| `friday/redact.py` | Credential scrubbing, on logs and on tracebacks |
| `friday/liveness.py` | Heartbeat, outage alerts, daily summary, and the promotion cadence |
| `friday/api.py`, `board/` | JSON API and the read-only page on `:8086` |
| `migrations/` | Alembic revisions |
| `tests/` | Driven through two seams: a fake `Provider` and a scripted model transport |
| `docs/` | `DESIGN.md`, `SPEC.md`, `agents/` |
| `.scratch/` | Local issue tracker |

Packaging: **explicit `__init__.py`**, not namespace packages. That is a
statement about PEP 420, not a licence to put implementation in `__init__.py` —
importing any submodule runs the parent's `__init__.py` first, so whatever
lives there is paid for by every import of the package.

There is no `nodes/`, `procedures/`, `tools/`, `memory/`, `permissions/` or
`hooks/` package, and their absence is a decision rather than an omission. The
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
  refusals, and classifier errors all route to `HITL` — never to a silent
  discard. A dropped mention is indistinguishable from correct operation.
- **Every model call goes through Chat Completions**, so `base_url`, `api_key`
  and `model` are the whole of what it takes to move an agent to a different
  OpenAI-compatible provider. Not the Responses API: some providers reject
  parts of it, and one of them is the one in `config.yaml`.
- **`friday/harness.py` is the only module that may import `agents`.** The SDK
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
  remember — see `_NEEDS_APPROVAL` in `friday/db.py`.
- **One persona, three modes, and the mode is per agent.** `PERSONA.md` says
  who the agents are and that people read Vietnamese; `config.yaml` says how
  much of it each agent takes. `full` for the two agents whose output a person
  reads, `language` for the ones filling in structured fields, `none` for the
  ones returning a path or a diff. This is not caution: triage fills in
  `environment` by tool call and `friday/validation.py` requires
  `production` / `staging` / `dev`, so an agent carrying the voice writes
  `sản xuất` and the reporter is asked to confirm what they already said. It
  goes in `instructions`, never the per-call bundle — shared bytes at the
  front of a prompt are the ones a provider's cache reuses across agents.
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
  tracebacks, or the task DB. `friday/redact.py` enforces this on the way out,
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
