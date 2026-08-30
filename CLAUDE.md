# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status

Still a scaffold. `agent/`, `core/`, `skills/`, and `tools/` are empty
directories; `gateway/discord.py` holds a single stub
(`build_channel_discord`); `run_agent.py` is the generated `uv init`
hello-world. No commits yet on `master` (default branch for PRs is `main`).

The design, however, is settled and written down — see **`docs/DESIGN.md`**,
which is the source of truth for what this is meant to become. Read it before
adding anything non-trivial. Decisions recorded there were reached
deliberately; if you think one is wrong, raise it rather than quietly building
something else.

Work is broken into tickets under `.scratch/discord-mention-triage/issues/`,
derived from `docs/SPEC.md`. Start at ticket 01.

Keep the split honest: this file describes what *exists*, `docs/DESIGN.md`
describes what is *agreed*. Do not document intent here as if it were
implemented.

## Environment

- Python **3.13** (pinned in `.python-version`), managed with **uv**.
- `pyproject.toml` declares no dependencies yet; the venv lives in `.venv/`.

```bash
uv sync                 # create/update .venv from pyproject + uv.lock
uv run run_agent.py     # run the entrypoint
uv add <package>        # add a dependency (updates pyproject.toml and uv.lock)
```

Prefer `uv run ...` over activating the venv manually, and let `uv add` edit
`pyproject.toml` rather than hand-editing dependencies.

## Layout

Target structure, from `docs/DESIGN.md`. **Nothing under `friday/` exists yet** —
ticket 01 creates it.

| Path | Contents |
| --- | --- |
| `run_agent.py` | Composition root — the only place adapters are constructed |
| `config.yaml` | Pool size, per-node models and caps, channel whitelist, thresholds |
| `friday/config.py`, `db.py`, `models.py` | Config loading; async SQLite + migrations; shared types |
| `friday/inbox/` | Deep module. Whole interface is `stream() -> AsyncIterator[InboundEvent]`; gateway, backfill, cursors and dedup are implementation and are not imported from outside |
| `friday/providers/` | `Provider` protocol; `providers/discord/` holds the bot client, the isolated user client, and normalisation |
| `friday/nodes/` | `Step` record, the manual model loop, prompt assembly, per-node checkpointing |
| `friday/triage/` | Classification node, plus threshold and follow-up policy |
| `friday/procedures/` | Procedure registry; `report_bug` and `tracing` ship as empty node lists |
| `friday/tools/` | Shared registry; side-effecting tools carry their own guards |
| `friday/memory/` | Staging tier, long-term notes, compaction pass |
| `friday/tasks/` | Transition verbs, the legal state graph, persistence |
| `friday/worker.py`, `liveness.py` | Bounded task pool; heartbeat and daily summary |
| `friday/board/` | Read-only FastAPI + HTMX page on `:8086` |
| `tests/` | Driven through two seams: a fake `Provider` and a scripted model transport |
| `docs/` | `DESIGN.md`, `SPEC.md`, `agents/` |
| `.scratch/` | Local issue tracker |

Packaging: **explicit `__init__.py`**, not namespace packages.

The root `agent/`, `core/`, `skills/`, and `tools/` directories predate the
design and map onto nothing in it — delete them when `friday/` lands.
`gateway/discord.py` is superseded by `friday/providers/discord/`.

## Architecture constraints

Load-bearing decisions from `docs/DESIGN.md`. Violating one is a design change,
not an implementation detail:

- **One process, one container.** Bot gateway, user gateway, worker, and the
  web server on `:8086` are all asyncio tasks in a single event loop. This
  follows from SQLite: multiple writers over a shared volume means contention
  and locking bugs.
- **SQLite is the only state store**, including gateway resume state
  (`session_id`, `seq`, `last_seen_message_id`). Anything that must survive a
  restart goes in the DB, never in memory. **DB access must be async**
  (`aiosqlite` or a thread executor) — a blocking call on the event loop stalls
  the Discord gateways.
- **Two Discord identities in one process.** `discord.py` for the bot,
  `discord-self` (namespaced `discord_self`) for the user account. Keep the
  user-side isolated in its own module — it depends on a private API and is
  expected to break.
- **Inbound messages are deduplicated on `(provider, provider_message_id)`.**
  Two delivery paths (gateway and REST backfill) feed the same pipeline, so
  every handler must be idempotent on that key.
- **Never drop a mention.** Low confidence, turn-cap and token-cap breaches,
  refusals, and classifier errors all route to `HITL` — never to a silent
  discard. A dropped mention is indistinguishable from correct operation.
- **All LLM calls go to OpenAI** via the Responses API, driven by a manual loop
  with `store=False`. Conversation state is held client-side and checkpointed
  to SQLite.
- **A procedure is data, not a coroutine.** Every unit of reasoning is a node
  declared as a frozen dataclass (prompt, tools, `output_schema`, model, caps);
  a procedure is an ordered list of them, checkpointed per node. A suspended
  coroutine cannot be persisted, so an awaited agent loop would lose its
  position across a restart.
- **Side-effect guards live inside tools, not in scope.** The tool registry is
  shared across all nodes, so `post_reply()` itself checks that the task is
  `review`-approved and returns a refusal as a normal tool result.
- **Agents never write long-term memory directly.** `remember()` writes to a
  staging tier; a compaction pass promotes only what an approved outcome
  corroborates.

## Conventions

- Test framework is decided but **not yet wired**: `pytest` + `pytest-asyncio`,
  to be added via `uv add --dev pytest pytest-asyncio`. Record the run command
  here once it exists.
- No linter or formatter is configured. If you add one, wire it through `uv`
  and record the command here.
- **Structured config in `config.yaml`** (pool size, per-node models and caps,
  channel whitelist, thresholds) — version-controlled, so changes are
  reviewable diffs. `.env` is for secrets only.
- `.gitignore` covers `__pycache__/`, build artifacts, and `.venv`.
- The Discord user token is unscoped account access — it must never reach logs,
  tracebacks, or the task DB.

## Agent skills

### Issue tracker

Local markdown under `.scratch/<feature-slug>/issues/`; the spec lives at `docs/SPEC.md`. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical roles, unchanged (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` + `docs/adr/` at the repo root (neither exists yet). See `docs/agents/domain.md`.
