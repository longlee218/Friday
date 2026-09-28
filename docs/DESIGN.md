# friday-agents — Design

This file holds the architecture. Its first part, **What exists**, describes
the system as built and is kept current: when a load-bearing decision
changes, correct it here in the same commit — nothing breaks when it is
wrong, which is how it goes stale. Its second part, **Reasoning**, records
why decisions were made; several were later reversed and are marked inline.
Where the second part disagrees with the first, or with the code, the first
part and the code are right.

Project state (what is running, which boards are open) lives in
`CONTEXT.md`, not here. How to work in the repo lives in `CLAUDE.md`.

# What exists

## Environment

Python **3.13** (`.python-version`), managed with **uv**. The agent runtime is
**`pydantic-ai-slim[openai,mcp]`** (pinned): `friday/kernel/harness/harness.py` is the
only module allowed to import it, and `mcp.py` and `llm_log.py` take its names
through the harness. It replaced `openai-agents` in ticket 05 — a stability
policy instead of a 0.x line, and structured output, typed run context and hook
capabilities as the framework's main paths rather than a hand-built tool, an
identity trick and a per-run mutation of a shared agent.

`web/` is React + Vite, built to static files that `friday/kernel/ops/api.py`
serves from the same process — one container, no Node at runtime.
`web/dist/` is never committed. There are deliberately no JavaScript tests
and no linter. Two guards compensate: `tests/test_web_hooks.py` (a narrow
check for a React hook called after an early-return `if`, which renders the
page blank with no visible error) and `tests/test_web_contract.py` (checks
the hand-written `web/src/api-types.ts` against the real converters' output
keys, since every route is typed `-> dict`).

Persistence is **SQLAlchemy 2.0 async** (`friday/store/schema.py` holds the
mapped classes; `friday/store/db.py`'s `Database` is the only store, a facade
over the repository mixins under `friday/store/repositories/`, and converts at
the edge) with **Alembic** migrations in `migrations/`. `run_agent.py` upgrades
to head at startup, before anything opens the database. Migrations run
**transactionally** (`transactional_ddl=True`) — SQLite can do DDL inside a
transaction, so a migration that fails partway cannot leave the schema ahead
of its stamped version. The database path comes from `config.yaml`, not
`alembic.ini`; `FRIDAY_DB` overrides it. `tests/test_migrations.py` fails if
`schema.py` and the migrations stop describing the same database, and it
also compares partial-index predicates, which `compare_metadata` does not
(autogenerate emits `pass` for a changed predicate).

## Running it

Friday currently runs on the operator's own machine (since 2026-09-16),
with what that machine has: the dev cluster through `ssh dev`, the backend
repos under `~/Documents/Apero/`, and the MCP sessions the operator
authenticates. Every tool a graph is given is a read.

The server path still exists:

```bash
docker compose build && docker compose up -d
docker compose logs -f
```

Secrets arrive at runtime from `.env`, never baked. `config.yaml` is mounted
read-only, so changing a model tier is a restart, not a rebuild; changing a
knob (a threshold, a budget) is a commit. The database is on a named volume —
without it a redeploy loses the cursors, and the sweep then only recovers the
last `MAX_MESSAGE_AGE_SECONDS` of each
watched channel. The board is unauthenticated by design and shows every
captured message and every model prompt, so it is published to loopback
only and reached with `ssh -N -L 8086:127.0.0.1:8086 <host>`.

## Layout

Since the kernel-consolidation (tickets 19–21), `friday/` holds only four
things: `sdk/` (the bottom of the stack — contracts plus the pure shared
values), `kernel/` (everything application-side — the invariants and all the
machinery that enforces them), `store/` (persistence, a top-level sibling the
kernel reaches directly — inverting that behind a `Store` Protocol in the sdk is
DESIGN-v2 deferred work), and `plugins/`'s mount is at
the repo root. Every module below that is not `sdk`/`store` lives under
`kernel/`; the rows keep their old one-word names for familiarity.

| Path | Contents |
| --- | --- |
| `run_agent.py` | Composition root — the only place adapters are constructed and asyncio tasks started. Reads no agent's knobs; runs `check_graphs` straight after `load_config` |
| `serve_board.py` | The board alone, against the live database, without connecting to Discord |
| `import_context_files.py` | One-off: import a second install's old per-channel YAML files as `origin=admin` memory rows |
| `config.yaml` | Provider keys, **named model tiers** (`tiers:`) and **install facts** only — channel whitelist, mention types, `workflows.concurrency`, MCP servers, `sensitive_words`, paths, the operator. Every **knob** is a named constant beside its user, pinned by `tests/test_knobs_are_constants.py`; a moved knob left in the file is refused at load (board `domains-plug-in`, tickets 07 and 17) |
| `friday/kernel/config.py` | Loads `config.yaml` and resolves `${VAR}`. `Config.agent(declaration)` resolves an agent — its tier's endpoint and key, its declaration's temperature, `max_turns` and `tokens` — and **an undeclared tier refuses the boot**. Folded into the kernel in ticket 21; imports only the kernel domain and the sdk, so it is safe to load early |
| **`friday/kernel/domain/`** | The vocabulary the kernel is written in, folded under it in **ticket 19**, split by responsibility in build-the-spine ticket 03: `messages.py` (`MentionType`, `InboundEvent`, `Artifact`), `tasks.py` (`Task`, `RunningTask`; plus `Params`, `SKIP`, `askable_fields`, `ExtractionMark` until ticket 16 deletes the extractor), `outbound.py` (`Outbound`, `payload_hash`, `AuditEntry`), `memory.py` (every memory type and refusal, the memory-kind data shapes and `RoomSummary`), `monitor.py` (`ToolCall`, `ModelCall`, `MessageFlow`, `MonitorEvent`, `MonitorSnapshot`), `state.py` (`FridayState`), `conversation.py`, `states.py` (`TaskState`/`OutboundState` transitions), `triage.py` (`Decided`/`NeedsHuman`/`make_decided` — what triage concludes), `memory_guard.py` (refuses instruction-shaped memory text). The pure, plugin-facing pieces that used to sit beside these — the workflow actions (`Ask`/`Reply`/`HandOver`), the validation DSL, the prompt primitives, `scrub`, the memory `Origin` — are the bottom of the stack now and live in `friday/sdk/`. Since ticket 12 it holds no memory-kind *catalog* — `MemoryKind` is a validated string, and the kinds register themselves (see `friday/kernel/memory/registry.py`) |
| **`friday/store/`** | `schema.py` the mapped classes; the store is a **facade over repositories** (ticket 16): `db.py`'s `Database` composes cohesive mixins under `repositories/` (`messages`, `memory`, `memory_candidates`, `outbox`, `tasks`, `task_conversation`, `compaction`, `calls`, `rooms`, `monitor`, `verdicts`, `audit`; `memory` and `tasks` split in build-the-spine ticket 03), with `_common.py` holding the shared imports, row converters and constants. Converts at the edge — nothing above it knows SQLAlchemy exists. It does its own **data integrity** (schema-fit, the per-channel cap, referential and admin-row checks, the unique natural key) but **not the trust-boundary invariants**: the outbox approval gate and the memory-write guard (instruction-shaped, origin-may-write) live in the kernel (`friday/kernel/outbox.py`, `friday/kernel/memory/write.py`), so a dumb or fake store cannot smuggle either past them (workflow state is DBOS's, excluded from the split) |
| **`friday/kernel/harness/`** | What it takes to call a model, nothing about what to call it for: `harness.py` (the loop; only module that may import the SDK), `retry.py` (which failures earn another attempt, and the attempts' bookkeeping), `model_client.py` (the provider's client, the answer shape's JSON schema), `structured.py` (declared-shape answers), `instruction_prompt.py`, `skills.py`, `mcp.py` (servers from configuration, stdio/SSE/streamable-HTTP), `auth.py` (Friday's own Keycloak client, per request), `llm_log.py` |
| **`friday/kernel/memory/`** | `registry.py` (ticket 12) — the one memory-kind registry: a kind registers a `MemoryKindSpec` (writers, data, cardinality, injected, and a per-kind `key` deriver); `writers_for`/`data_of`/`cardinality_of`/`injected_of`/`natural_key` read it, and **readers are inverted** — each reader declares the kinds it needs (`register_reader`, which merges), so `readers_for`/`domain_kinds` derive from that and a kind names no reader (DESIGN-v2 §9.2). Registers the **core** kinds only — `fact`, `constraint`, `decision`, `voice`, `summary`, `finding`, `person`, and `skill` (which replaced `runbook` in ticket 14, procedures being skills not a kind, §6.3); the **pack** kinds (`backend.*`) ship with their plugin and register through `register(api)`. `channel_context.py` — the summariser: one active `summary` row per watched channel. `verdicts.py` — the operator marking a classification right. Every memory is a row in `memories` |
| **`friday/kernel/ops/`** | Alive and safe, deciding nothing: `liveness.py`, `redact.py`, `api.py` (the board's API, including the operator's memory routes) |
| **`friday/kernel/text_transform.py`** | Splits code out before cleaning prose (was `text/transform.py`, folded in build-the-spine ticket 03); `friday/kernel/text/param_hygiene.py` cleans one value until ticket 16 deletes it |
| `friday/kernel/inbox.py` | `stream()`, `sweep_once()`, `tally()`. Gateway, backfill, cursors and dedup are implementation |
| `friday/kernel/providers/` | `Provider` protocol; `providers/discord/` holds `user.py`, `bot.py`, `normalise.py`. `__init__.py` stays empty on purpose |
| `friday/kernel/triage/` | Classification only, plus its sensitive-word prefilter and the untriaged-message loop. `context.py` gathers what a mention is shown; `prompt.py` renders it. The answer is a `Decided` (`type: str`, `confidence`); the **closed set the model is held to is built at boot from the registry** by `friday.kernel.domain.triage.make_decided` (ticket 11) — a `Literal` over the registered task types plus `skip`, validated exactly as the old static one, only the set is now the registry's, not a hand-maintained `DECISIONS` |
| `friday/kernel/extraction/` | Everything a task knows, lifted out of the reporter's own words. One extractor per task type, each owning its prompt and `Params` schema; one `extractor` config block serves all of them. `context.py` is node 0's gather function: transcript, domain memories (this room and `'*'`), outstanding questions and `known`, one call, one frozen `FullContext` |
| **`plugins/backend/sources/`** | **Decided 2026-09-28, not built** (board `domains-plug-in`, ticket 09): `sources/` folds into `plugins/backend/toolsets/` — one file per data source holds both the tools and the client that reaches out, so there is one place to control. The guard's allow-list moves to `toolsets/`; replay is unaffected, it swaps `Reads` (`CannedReads`), not the source class. Today: where facts come from, and the only place that reaches an outside read surface: `logs.py` (`LokiSource`, `SshKubectlSource`), `code.py` (a stack frame mapped into the operator's clone, and the window around it), `release.py`, `db.py`. Read-only by construction — no verb here writes — and holds no judgement: which window, which service, which frame all arrive as arguments. Implements the sdk source ports and **imports `friday.sdk` only** (ticket 14). `tests/test_sources_are_the_only_door.py` is the guard, and it reads `friday/` and `plugins/` both |
| `friday/sdk/` | **Contracts only** — Protocols and dataclasses, no I/O, no third-party imports — what the kernel builds on and a plugin codes against. `workflow.py` is the workflow **port** (`DAG`, `Node`, `Edge`, `Deps`, `DAGState`, `envelope`, `Ask`/`Reply`/`HandOver`; no `dbos`, no `DAG.version` — recovery is DBOS's, ticket 06). `plugin.py` holds `Plugin` (`id`, `register`, `enricher`), `PluginAPI`, `TaskTypeSpec` (until ticket 16); build-the-spine ticket 05 added the spine's declarations beside it — `action.py` (`Action`, `Recognition`, `ActionContract`, `Limits`), `AgentSpec`/`Budget` in `agent.py`, `ToolsetSpec`/`RunContext` in `toolset.py`, registered through `api.action`/`api.agent`/`api.toolset`; `memory.py` holds `MemoryKindSpec` (with a per-kind `key` deriver), `Origin`. **Ticket 14** added the ports a real plugin codes against: `sources.py` (`LogSource`, `CodeSource`, `Placement`, `Lines`, `Reads`, and `TOOL_CALL_TIMEOUT_SECONDS`, the one time limit left), `agent.py` (`AgentDeclaration` — an agent's tier, temperature and `(max_turns, tokens)` budget, build board `build-the-spine` ticket 01), `toolset.py` (`tool`/`ToolSpec`/`ToolContext`, a neutral tool the harness binds to the vendor's; was `tools.py`, renamed in build-the-spine ticket 03 — the harness's own `tool`, which builds the vendor's `Tool`, stays in `harness.py` until ticket 08 moves the core tools onto `ToolSpec`), `model.py` (`Model` — the `run_structured`/`last_error` surface a plugin's model node calls), `outbox.py` (`Kind`). **Ticket 19** made the sdk the true bottom of the stack: the pure logic a plugin needs lives here outright, no longer re-exported from a value layer — `actions.py` (`Ask`/`Reply`/`HandOver`, which `workflow.py` re-exports as node vocabulary), `prompt.py` (the prompt-assembly primitives), `redact.py` (`scrub`), `validation.py` (the params rule DSL) — and `memory.py` **defines** `Origin`/`MemoryOrigin`. It imports **nothing of ours** |
| `friday/kernel/` | Owns the invariants and **names no plugin**. The kernel-consolidation (tickets 19–21) folded every application module in here: `registry.py`/`plugin_host.py` (the `PluginAPI` a plugin's `register(api)` fills, and the loader that runs it), `domain/` (messages, tasks, outbound, memory, monitor, state, states, conversation, triage outcomes, the memory-write guard — ticket 19, split in build-the-spine ticket 03), `dag/` (the graph home: port callers, node 0, the registry, the router, and the DBOS `adapter.py`), `harness/` (was `agent/`), `pool/` (was `tasks/`), `memory/`, `outbox.py` + `outbox_card.py`, `triage/`, `extraction/`, `responder/`, `inbox.py`, `ops/`, `text_transform.py` + `text/`, `tools/`, `providers/`, `config.py`. Imports `friday.sdk`, itself, and the concrete `friday.store` — the one standing exception the guard names, since inverting the store behind a `Store` Protocol in the sdk is DESIGN-v2 deferred work (ticket 16 split the store into repositories and moved its *invariants* into the kernel, but the kernel calls ~70 store methods, so it does not build that Protocol). `tests/test_dependency_rule.py` is the guard: sdk imports nothing of ours, kernel imports only sdk (+ the store, interim), a plugin imports sdk only, and the kernel carries no plugin import or task-type/pack-kind literal (the kernel owns none since build-the-spine ticket 02) — **non-vacuous since ticket 14**, when `plugins/devops/` (now `plugins/backend/`) became the first real plugin |
| `friday/kernel/plugin_host.py` | Loads the plugins named in `config.plugins` (default `plugins.backend`, `plugins.ops`) and validates each one's config block against its `Plugin.config` (temporary; ticket 09 turns the blocks into constants). **One lifecycle** (build-the-spine ticket 05): `load_plugins` runs each plugin's `register` once against its own `PluginRegistration` (`registry.py`), then runs `boot_refusals.refusals` over the whole `Registry` and raises `BootRefused` (a `ConfigError`) with every reason. The memory registry and `register_all` each load and read what that load recorded (boot still loads more than once until ticket 16 deletes the DAG path); `register_all` attaches the boot caps (`Loaded.attach_caps`) before building the task-type graphs, whose builders read `api.caps` lazily (ticket 16 deletes `caps`). **Boot refusals**, all offline, one test each in `tests/test_boot_refusals.py`: a duplicate name (the `Registry`); a name not under `<plugin.id>.`; an agent tier not in `tiers`, an MCP server not in `mcp_servers` (both skipped for a config that carries neither, the memory-kinds default); a dangling agent→toolset, contract→agent/toolset or reader→agent (except `code` — enforced only for a plugin that registers an `AgentSpec`, until 16); a granted toolset of another domain type (the enricher's return annotation); the recognition checks; a grant of another plugin's agent/toolset; a missing agent/toolset description. A `core.shell` host check lands with `core.shell` (ticket 08). Folded into the kernel in ticket 21: it names no plugin statically (it loads them by name with `importlib`), so both guards hold |
| `friday/kernel/dag/` | `adapter.py` — the **DBOS adapter** beneath the port, the one module that imports `dbos`. A graph is a `@DBOS.workflow` walk, each node a memoized `@DBOS.step`; DBOS owns run persistence, step memoization and resume. The kernel chain (retry, redaction, the `node_runs` record) is ported into `_invoke`, not delegated. An ast guard keeps `dbos` here |
| `friday/kernel/dag/` | `prepare.py` builds the entry node (node 0) every graph shares. **`registry.py` is the one task-type registry** (ticket 11): a task type registers a `TaskTypeSpec` (params, graph, needs, run-deps) — the map that used to be `PARAMS` + the extractor map + the router's `_graphs` — and the built DAG is stored beside it. `task_types.py` loads the plugins (the kernel registers no task type of its own since build-the-spine ticket 02) — its `BootContext` is the **caps** a plugin's graph builder reaches through (`prepare_node`, `make_harness`, and `simple_dag` for an action whose whole graph is node 0); `router.py` is now generic — it reads the registry, builds `EDGE_ROUTER` and (ticket 13) checks that every type's `deps` factory can build its run `Deps`, and **names no task type**. The `api_issue` investigation graph, its sources and its typed per-run `Deps` moved out to `plugins/backend/` in ticket 14. `adapter.py` is the **DBOS adapter** (the one module that imports `dbos`), folded in here when `dag/` and `workflow/` merged (ticket 20); the old `engine.py`/`state.py` re-export shims were deleted then, and the graph vocabulary is imported from `friday.sdk.workflow`/`friday.sdk.workflow_state` directly (the hand-written `DAGRunner` was retired onto DBOS) |
| **`plugins/backend/`** | The first real plugin (ticket 14), and the proof the split holds: **imports `friday.sdk` only**. `__init__.py` exposes `PLUGIN` + `register(api)`, which contributes the `backend.trace_problem` and `backend.answer_question` task types, its pack kinds (`backend.service`/`.route`/`.environment`/`.project`/`.dependency`, in `memory.py`) and their reader routing. `params.py` (`ApiIssueParams`), `config.py` (`BackendConfig`, incl. the container-root policy as data — box 4), `graph/` (the investigation graph `intake → acknowledge → diagnose loop → report`, its diagnose prompt and `ApiIssueDeps`), `sources/` (`LokiSource`/`SshKubectlSource`/`ReleaseSource`/`DbSource` and the code reader, implementing the sdk ports), `investigate.py` (the diagnose read tools). The heavy handle it cannot import — the diagnose `Harness` — is injected by the composition root through the boot caps; node 0 is now the plugin's own deterministic `intake` node, not the shared `prepare_node` (board `build-the-loop`) |
| **`plugins/ops/`** | `ops.request_permission` (build-the-spine ticket 02 moved it out of the kernel; `backend.answer_question`, the `docs` plugin of ticket 15, folded into `plugins/backend/answer_question.py` then). No investigation past node 0, so the plugin is `__init__.py` (`PLUGIN` + `register`) and `params.py` (`AccessRequestParams`) alone; its whole graph is the shared simple node-0, built through `api.caps.simple_dag`, so it imports `friday.sdk` only. Configured plugins are listed in `config.plugins` (default `plugins.backend`, `plugins.ops`). Action names are `<domain>.<action>`; `/api/actions` serves `[{name, domain}]` and the board colours a tag by domain |
| `friday/kernel/pool/` | The pool: runs node 0 itself each pass, then starts or resumes each task's durable DBOS workflow (`task-<id>`) and polls it to its next boundary — the outcome, or the `Ask` it suspended on. A workflow waiting on the reporter suspends and never blocks other tasks. Decides nothing about what a graph decides |
| `friday/kernel/tools/` | Every tool an agent may call, one module per subject: skills (`fetch_skill`, `search_skills`, `describe_skill`, `read_skill_file`), memory (`memory_search`, `memory_add`, `memory_propose`, `memory_update`, `memory_delete`, scoped per channel, wired to the responder). `tests/test_tools.py` asserts the full list and forbids declaring a tool anywhere else (one exemption, below) |
| `friday/kernel/responder/` | Drafts a reply in the operator's voice |
| `friday/kernel/outbox.py` | Nothing is sent by a caller: it is a row, and one loop delivers it. `outbox_card.py` renders the approval card |
| `web/` | The operator monitor, on `:8086`. React + Vite SPA served by `ops/api.py`. Live feed via SSE (`/api/events`), drill-down to a Flow screen, breadcrumbs; the Rooms memory dialog writes, corrects and removes the operator's rows; a Workflows panel (ticket 08) buckets the durable workflows — running / queued / succeeded / failed — from `/api/workflows` (which maps `DBOSClient.list_workflows` out of DBOS's vocabulary in the adapter; no Conductor), refreshed on the `workflow` SSE event a finished graph node publishes. Palette and motion tokens live in `web/src/index.css` under `:root` — no component may hardcode a hex literal or inline `style={{}}` (`tests/test_web_tokens.py`). Shortcuts in `web/src/keyboard.ts` (`?` opens the overlay) |
| `migrations/` | Alembic revisions |
| `tests/` | Driven through two seams: a fake `Provider` and a scripted model transport |
| `docs/` | `DESIGN.md` (this file), `SPEC.md`, `agents/` |
| `.scratch/` | Local issue tracker: one board per feature |
| `evals/` | The classifier's regression net: `triage.jsonl` (frozen), `build_triage_set.py` (refreshes it by hand), `run_triage_eval.py` / `run_api_issue_eval.py` (score the live agents — call the configured provider, not run by the suite). The runners are `pydantic-evals` `Dataset`/`Case`/`evaluate`; the aggregate metrics stay Friday's (`scoring.py`, `api_issue.py`). See `evals/README.md` |

Packaging: **explicit `__init__.py`**, not namespace packages — importing
any submodule runs the parent's `__init__.py` first, so only re-exports
belong there. There is no `procedures/`, `permissions/` or `hooks/`
package — build one when a second caller needs it, not before. A node is a
function inside the graph that owns it (`friday/kernel/dag/`), not a `nodes/`
package.

**Every tool lives in `friday/kernel/tools/`.** `tests/test_tools.py` asserts the
tool list — built from the registered factories, not `vars(module)`, so a
tool living in a closure is still caught — and forbids declaring one
anywhere else. The answer an `Harness(answers=...)` agent gives is **not** a
tool in that list: it is the run's *output*, a Pydantic AI `ToolOutput`
generated per shape from the shape's own fields, so `harness.py` declares no
tool of its own; `test_the_answer_is_a_run_s_output_not_a_door_an_agent_chooses`
pins that.

`harness.tool` wraps Pydantic AI's `Tool`; the "failing tool body tells the
model 'unavailable, carry on'" rule lives in the run's hooks
(`llm_log.py`'s `tool_execute_error`), where the substitution and the record
are one decision. A `ModelRetry` a tool raises is passed through untouched —
the one failure the model can fix inside the same run — and bad-argument
failures Pydantic AI retries for us before the body runs. The answer output
tool is *forced* (no text output is allowed), so the run ends the moment the
model calls it. `ToolContext` is an alias of `RunContext`, hidden from the
tool's JSON schema by design, and a tool reads its per-run state off
`ctx.deps`. Four tools reach a skill, split by what the agent already knows:
`fetch_skill` by name, `search_skills` when it has no name,
`describe_skill` for metadata, `read_skill_file` for a path a body linked
to.

## Load-bearing constraints

Violating one is a design change, not an implementation detail. Within a
bullet, the first sentence is the rule; the rest is mechanism and why.

### Process & storage

- **One process, one container.** Bot gateway, user gateway, worker and the
  web server (`:8086`) are asyncio tasks in one event loop — required by
  SQLite, since multiple writers over a shared volume means contention.
- **SQLite is the only state store**, including per-channel cursors and
  every memory. Anything that must survive a restart goes in the DB — except
  gateway session state, which the library owns. **DB access must be
  async** — a blocking call stalls the Discord gateways.
- **Two SQLite files, one state, one lock** (§12.1, ticket 09): the
  application db and DBOS's workflow *system* db (`<db>.system.db` beside it).
  The single-instance lock (ticket 04) covers both — one process owns them.
  **WAL is on for both** (the app db in `store/db.py`, the system db set by
  `kernel/dag/adapter.py` `launch` before DBOS opens it), so a board read
  never blocks a writing workflow.
- **Startup recovery brings both sides back together.** DBOS resumes any
  `PENDING` workflow on `launch` (the durable step replays without re-running
  a completed node); Friday's inbox sweep backfills each channel from its
  cursor on reconnect and on its timer. Neither is triggered by hand — a
  restart is just a start.
- **A daily online backup covers both files** (`kernel/ops/backup.py`, ridden
  on the heartbeat like model-call trimming): `sqlite3`'s backup API takes a
  consistent snapshot of each while the process runs, keeping
  `KEEP_BACKUPS` days under `backup_dir`; both halves of a day are kept or
  pruned together. **Restore is a stopped-process command** — with the agent
  down, `uv run python -m friday.kernel.ops.backup restore <YYYYMMDD>` copies
  that day's two files back over the live paths (it refuses a day missing
  either half, so a task is never restored without its workflow).

### Discord identity & ingestion

- **Two Discord identities in one process.** `discord.py` for the bot
  (`providers/discord/bot.py`), `discord-self` for the user account
  (`providers/discord/user.py`, expected to break — a test enforces it is
  imported only there). `providers/discord/__init__.py` stays **empty**.
  `is_own` only knows the user gateway's identity; `Database.we_sent` is the
  real "is this ours?" check and the inbox calls it on every message.
- **Inbound messages are deduplicated on `(provider, provider_message_id)`.**
  Every handler must be idempotent on that key.
- **Never drop a mention.** Low confidence, cap breaches, refusals,
  classifier errors and the sensitive-word prefilter all route to a person.
  A turn older than `MAX_MESSAGE_AGE_SECONDS` is recorded `outdated` — a row, a
  reason and a board entry, no reply. The same window bounds a cold cursor's
  backfill, which *is* an unrecorded drop, logged at boot.
- **`sensitive_words`** blocks a message from reaching the model, checked
  before the call — it **holds**, not skips.
- **The operator's own messages end the work and open nothing** — except a
  message from the watched account tagging itself. That task goes to
  `handled_by_operator`, and everything queued about it withdraws.

### Model calls

- **Every model call goes through Chat Completions**, not the Responses API.
  Moving providers is `base_url`/`api_key`/`model` alone.
- **Every model call is bounded and logged at one seam.** `_settle` runs it
  under the agent's budget — `UsageLimits(request_limit=max_turns,
  total_tokens_limit=tokens)`, `max_turns` counting every request **tool turns
  included** (the output correction rides on top, an attempt not a turn) —
  and hands the call to the recording sink given to a `Harness` **at
  construction**. **One run of a harness at a time** (`Harness._one_run`): the
  pool runs graphs side by side, and `last_error` and `unfit` are read off the
  instance.
- **No time budget anywhere** (board `domains-plug-in`, ticket 17). A run
  stops on turns or tokens; the only clock is `TOOL_CALL_TIMEOUT_SECONDS` on
  each tool call — `Reads.call`, the MCP toolset's `read_timeout`, SSH
  `kubectl`, `git show` — so a hung read cannot hold a pool slot. **One model
  request** is bounded by its agent's `request_timeout_seconds` (Pydantic AI's
  `ModelSettings.timeout`; 30s triage/extractor/summary, 60s responder/diagnose,
  set from measured latency on 2026-09-28). The HTTP client reads that number
  as a limit on silence between bytes, so `_attempts` also bounds each attempt
  at timeout × the requests it may make; an attempt past it is tried again.
- **Structured answers.** `Harness(answers=<dataclass>)` declares the shape;
  `run_structured(prompt)` returns an instance or `None`. The model answers
  through a generated tool call — **not** `response_format: json_schema`,
  which the configured provider accepts and silently ignores — validated
  in-process (`fits`). One correction, and it is the turn budget. A refusal
  never quotes the failing value back.
- **`friday/kernel/harness/harness.py` is the only module that may import the agent
  SDK** (`pydantic_ai`, `fastmcp`), the one exception being
  `friday/sdk/testing/` (the test-double seam). `tests/test_harness.py`
  enforces this.
- **A hiccup is retried here and nowhere else.** `Harness._attempts` retries
  connection errors, timeouts, 429 and 5xx — never a 400 — with the client's
  own retry off. **Attempts are core constants**, the same for every agent:
  `PROVIDER_ATTEMPTS 10` with a fixed `PROVIDER_BACKOFF_SECONDS 10` (no
  doubling — so a stalled provider can hold a slot ~690s for a 60s agent),
  `OUTPUT_CORRECTIONS 1`, `OUTBOX_ATTEMPTS 3`. Retrying stays in our loop
  rather than Pydantic AI's retrying transport so every attempt is recorded. There is
  no daily token ceiling; the heartbeat reports spend.
- **One state travels a message's whole journey, and it is read-only.**
  `FridayState` is the SDK's per-run dependency (`RunContext.deps`); every
  change is a named method returning a new state. `tests/test_run_context.py`
  enforces it.

### Workflow / DAG

- **Workflows are deterministic Python; an agent is a node inside one.** A
  model never chooses the next step. Durability is **DBOS's** now (ticket 06):
  a graph is a `@DBOS.workflow` walk, each node a memoized `@DBOS.step`, run on
  DBOS's own SQLite system database beside the application one — so a crash
  resumes from the last incomplete step, DBOS's per-step memoization in place
  of the hand-written checkpoint and the derived `DAG.version` (both gone).
  **Every node still runs through the adapter's `_invoke`**: retry over an
  explicit exception list
  with doubling backoff, any other exception as a `{status: error}` envelope,
  one `node_runs` row per attempt — the kernel chain the kernel keeps for
  itself rather than delegating.
- **`Ask` suspends; `HandOver` is terminal.** A node that cannot finish without
  the reporter returns `Ask` and the workflow suspends in place on `DBOS.recv`;
  when the reporter answers, the pool re-extracts the parameters and the same
  node **re-runs** with them (only that node, not the graph from the top). A
  `HandOver` escalates to the operator out of band, so it flows on as a result
  the pool reads. A workflow's input is a serializable scope key; the live
  `Deps` (the store, the log sources) are rebuilt inside the run.
- **An undeclared tier refuses the boot**: `check_graphs` builds every graph
  straight after `load_config`, which resolves the extractor's and each model
  node's agent through `Config.agent`.
- **Every task type is a graph. All but one get a single node** — extract,
  validate, then ask or hand over.
- **`api_issue` has an investigation past node 0**: `Intake → Acknowledge →
  Diagnose (loop) → Report`, in `plugins/backend/graph/`, which owns its nodes,
  its prompt and the one agent behind them (board `build-the-loop`, 2026-09-27).
  This replaced the older fixed pipeline `Prepare → Resolve → FindRequestLog →
  ReadFailingCode → Diagnose → Report`:
  - **`Intake`** (deterministic, no model) is node 0. It folds the old
    `Prepare` (extractor) and `Resolve` (env/service table lookup) into one
    pass: it derives placement from the `environment`/`service` rows, pulls
    cheap regex `Hints` (correlationId, artifact ids), retrieves memory/skills,
    and passes the reporter's raw text straight through. It runs fresh each pass
    and is never checkpointed; its **placement identity** is the staleness key.
  - The fixed **`FindRequestLog`/`ReadFailingCode`** pre-fetch nodes are gone —
    the **`Diagnose` loop reads for itself** through tools (`read_log`,
    `read_code`, `what_code_means`), and ends by returning `Diagnosis`, `Ask`
    (via `ask_reporter`) or `HandOver` (via `hand_over`).
  Three rules hold it together, and each replaces a way the deleted pipeline
  went wrong:
  - **Every fact about where a request went is a row, not a rule in code.**
    `environment` rows say what a domain suffix means, longest suffix
    winning, so the convention (`aperogroup.ai` is production,
    `dev.aperogroup.ai` is dev) and its exceptions are the same mechanism;
    `route` says which service a host is; `service` says where that runs
    (D3). **A missing row is a hand-over, not a guess** — guessing which pod
    serves a domain reads another product's logs. This amends D1's
    "environment from the domain, by rule, in code" (operator, 2026-09-21):
    a module naming one company's domains is an installation compiled into
    the system, and the rule was already wrong about that company.
  - **A node that cannot do its job skips out loud**: an envelope with a
    reason, rendered on the board and carried into the report. The graph that
    was deleted skipped every node on every run and said nothing.
  - **A diagnosis must point at evidence it was shown.** `Diagnosis.refs` are
    line ids, not quotes — measured (ticket 16): the configured model quotes
    a JSON log line right 32 times in 40 and points at one 20 times in 20, so
    the model names lines and code puts the text back. A pointer that
    resolves to nothing voids the answer.
  Only `Diagnose` calls a model.
- **Intake gathers metadata; the model reads for itself** (operator,
  2026-09-22, spec "Architecture v3.3" — **built** on board `build-the-loop`,
  2026-09-27). The fixed formulas that chose a needle, a window and when to
  widen were judgement wearing a rule's clothes, and each was measured getting
  it wrong within a week. So `Intake` produces only *where things are* — repo,
  service, cluster, namespace, environment, running tag, readable databases,
  stack — and the `Diagnose` loop fetches through tools. Because the loop reads
  code and docs, not only logs, a case with no matching log environment
  (`env == "external"`) is still investigable, so a curl/correlationId is no
  longer a precondition to open one (see CONTEXT.md § Action; ADR 0002).

  **A tool is not the back end, and the boundary is a measurement**: one raw
  `loki_query_range` window is 171 KB ≈ 43,654 tokens, and what `distil`
  leaves of it is 8 lines. `read_log` wraps the narrowing, the cut, the
  histogram and the numbering; the model decides what to look for and code
  decides what comes back. That also retires the Collector sub-agent — it
  existed to keep raw volume out of the reasoner, and a tool that distils
  has already done that.

  Its price is stated where it is decided: several model calls instead of
  one, and an investigation that is no longer deterministic — which makes a
  labelled case set (ticket 14) a precondition rather than a nicety.

- **Three layers, and the graph is the top one.** (**Decided 2026-09-28, not built** (board `domains-plug-in`, ticket 09): `sources/` folds into `plugins/backend/toolsets/` — one file per data source holds both the tools and the client that reaches out, so there is one place to control. The guard's allow-list moves to `toolsets/`; replay is unaffected, it swaps `Reads` (`CannedReads`), not the source class.) A **source**
  (`plugins/backend/sources/`) is a capability: it reads one kind of thing and decides
  nothing. A **check** is a formula over sources — `FindRequestLog` is "the
  correlationId's lines, else path plus identifier, in a window measured back
  from the reporter's message". A **node** is the frame a run is timed and
  retried in, and the unit DBOS memoizes. Reuse lives in the first layer, not
  the third: a node is a boundary, not a unit of reuse. Reordering a graph is a
  change to its `edges` in one function; a code change to a node makes DBOS's
  application version move, and only same-version runs auto-resume — so an
  in-flight run started under old code is not resumed onto new (ticket 06,
  replacing the derived `DAG.version` digest).
- **The code a diagnosis quotes is checked against the version that is
  running.** The operator's rule is that the image tag *is* the release tag,
  so `ReleaseSource.running_tag` asks the cluster which tag a service
  deploys and the code node compares the clone's copy of each file against
  it. Identical and it says so; different and it shows the tag's copy and
  says that; unresolved and it says that too. **Never a checkout and never a
  worktree** — `git show <ref>:<path>` reads the blob without touching a
  clone that is open in somebody's editor, and a ref that could be read as
  an option never reaches the command line. Until 2026-09-22 the node
  carried a standing caveat instead ("read at the clone's current HEAD …
  they may differ"), which is an admission rather than a check: measured by
  hand the day before, production ran `0.4.4` while the clone sat on
  `develop`, and the files happened to match.
- **Narrowing a read belongs to the source, because a `limit` is a tail and
  not a sample.** `LogSource.lines` takes a `needle`; Loki turns it into
  LogQL's `|=` and `kubectl` into a `grep` on the far side. Measured against
  production, 2026-09-21: a 35-minute window of `backend-reelme-v2` is
  ~12,400 lines, `limit=400` returned the newest 84 seconds of it, and the
  request under investigation had happened 23 minutes earlier — so the node
  distilled a dossier that could not have contained it. Filtering after the
  read cannot recover a line the read never fetched. Which back end can
  narrow a read, and how, is exactly what this layer exists to know; the
  check's own `distil` still runs either way, which is what lets a source
  that cannot narrow ignore it. The node reads twice per window, narrowed
  and whole, because the error-code histogram counts the window.

- **Friday carries the operator's own session, obtained once by a person and
  refreshed for ever after.** This replaced a `client_credentials` grant on
  the operator's call (2026-09-21): that version argued for Friday holding an
  identity of its own, which is the better answer *when a server will issue
  one*. These will not — `devops-generic` and `db-generic` authorise a
  person, and no service account is on offer.

  So `uv run authorize.py <server>` runs the sign-in once, interactively, and
  what is kept is a **refresh token** at mode 0600 under `data/credentials/`,
  outside the database because the database is copied, rendered and rewritten
  by migrations. `friday/kernel/harness/auth.py`'s `SsoTokens` is an httpx auth
  handler rather than a header, so the token is decided per request and a
  refresh needs no reconnection; the rotated refresh token is written back
  every exchange, and a 401 is retried exactly once.

  **Nothing about the server is configured** (measured 2026-09-22): each is
  its own authorization server and advertises everything at
  `/.well-known/oauth-authorization-server` — `authorization_code` +
  `refresh_token`, PKCE `S256`, a registration endpoint, and a public client
  with no secret. `authorize.py` discovers the endpoints, registers this
  machine, and writes the token endpoint and client id beside the refresh
  token, so `config.yaml` needs `url` and `auth: {}` and nothing that can go
  stale. `auth: {}` and a missing `auth:` are deliberately different answers.

  **What that costs, said out loud.** Friday reads as the operator, and the
  server's log will say so. The guards that remain are the ones that were
  always doing the work — what a reader declares it may call, what the server
  filter allows, and what the database grants — and none of them ever
  depended on which identity was presented. Proven against the live server
  on 2026-09-22: it offers about sixty tools and Friday is handed one.
- **Which tools a server may be asked for is declared in code**, on the
  class that calls them (`plugins/backend/sources/logs.py`'s `LokiSource.TOOLS`), and
  enforced twice: the server is built with a filter over those names, and
  every call goes through `friday.sources.Reads`, which refuses one no
  reader declared. `config.yaml` may not widen it and an `allow:` key there
  is refused at load. The server this actually talks to offers
  `release_rollback` and `godaddy_dns_edit_record` beside its log tools.

### Outbound & approval

- **Nothing is sent by the caller that decided to send it.** An outbound
  message is a row; one loop delivers it. **Approval belongs to the row**:
  `outbox.approved_at`/`approved_by`. The store's `sendable_outbound` query
  pre-filters on it, but the **authoritative gate is the kernel outbox**
  (`friday/kernel/outbox.py`, ticket 16): it re-checks a real, frozen approval
  before every send, so a store cannot make an unapproved reply sendable on
  its own. An approval card names the row it approves (`outbox.approves`); a
  card from before that change records nothing and says so.
- **The approval card tells the whole truth** (`friday/kernel/outbox_card.py`,
  DESIGN-v2 §12): built in the kernel — not the Discord adapter, which only
  transports it — it shows the exact bytes that would go out, the destination
  and audience (a reply is public), what any link or mention actually resolves
  to underneath its display text, and flags a secret found by pattern or by
  value. **Redaction runs on the draft**: `pool._propose` scrubs the reply
  before it is queued, so a secret never leaves even if the card is approved,
  and the card shows those same scrubbed bytes.
- **An append-only audit log** (`friday/kernel/audit.py`, table `audit_log`)
  records who approved which bytes, a decision refused because the decider was
  not the operator, plugin loads with their trust tier, and each MCP server's
  tool grant (on change). The kernel only appends — the store has no update or
  delete for the table; a hash chain over the rows is deferred (§16).
- **`auto_ask_for_details`** is the only path with no human in it;
  `friday/kernel/responder/check.py` is its floor. It is off in this repo's
  `config.yaml`.
- **SSE events come from the store rows the system already writes.**
  `record_model_call`/`record_tool_call` publish on the in-process
  `EventBus` after commit; `/api/events` replays from the bus on reconnect
  (`Last-Event-ID`).
- **Nothing takes a dangerous action, so there is no second gate.** The
  `Harness.checkpoint`/`resume` pair that could pause a run for approval was
  deleted in ticket 05 (nothing called it); Pydantic AI's deferred-tool
  mechanism is where it would be rebuilt if human-in-the-loop returns.

### Triage & extraction

- **Triage classifies and nothing else — a closed-set `Decided`.** An
  out-of-set answer is counted apart from an outage. Every `Params` class
  carries its own docstring, the type's description in the enum.
- **The unit is a turn, not a message**, computed when read, never stored.
- **Triage sees the turn plus the room's summary row**, gathered by
  `friday/kernel/triage/context.py`'s `build_light_context` into
  `LightContext(turn, summary)` from `Database.room_summary`. Domain memory,
  task parameters and artifacts do not reach triage.

### Prompts & voice

- **Only the responder carries a voice**, in its `instructions`, never the
  per-call input. `Reply` is built in exactly one place.
- **Three prompt families, one module each, no shared text** —
  `friday.kernel.triage.prompt`, `friday.kernel.extraction.prompt`,
  `friday.kernel.responder.prompt`; shared mechanism is `assemble(*sections)` and
  the escaping at a section's boundary, both `ast`-tested. A section
  describing a tool renders only if the agent has it; `trust_boundary` is
  claimed only by agents whose input wraps something. The responder reads
  `channel_derived` only, and its section order is load-bearing.
- **Anything stored and later read back into a prompt is stored plain**,
  escaped once at the seam. `channel_derived` takes the `summary` row and
  reads `RoomSummary`'s four fields by name, so the row's bookmark never
  reaches a prompt. A channel is resummarised only when it has said
  something since the bookmark; a rebuild supersedes the previous row.

### Memory

- **Every memory is a row in `memories`, in one of twelve kinds**, with
  `origin` (`model` | `admin`), `key` and `data`. `readers_for(kind)`
  decides who reads a row and `writers_for(kind)` who may write it,
  enforced at `Database.memory_add`. A model is offered five kinds only
  (`ModelMemoryKind`: fact, constraint, decision, finding, voice); `route`,
  `service`, `project`, `dependency`, `person` and `runbook` are the
  operator's, and five of them are read by code, never by a model. A
  structured kind's `data` is validated with `fits` at write time; one
  active row per `(channel, kind, key)` — except `finding`, which piles up
  so a diagnosis can read the newest few. A model-origin call cannot
  update, supersede or delete an admin row.
- **Memory reaches a model only as a tool result or an injected section,
  never through `instructions`.** Scope is runtime-supplied on
  `FridayState`; ids are opaque and sparse. The responder's tools see
  `voice`; the extractor gets the domain kinds of this room and `'*'` by
  injection, rendered by `room_facts` with the operator's rows first.
- **`check_not_instruction_shaped` refuses a line that reads as a command
  *and* names this system's own moving parts**, at the one write path,
  `Database.memory_add` — `text` only, never `data`. The operator writes
  through `POST/PUT/DELETE /api/channels/{id}/memories`.
- **Verbatim material (code, stack traces, `curl`) is stored whole as an
  `Artifact` row, never paraphrased.** The summariser reads it replaced by
  `[artifact id: description]`, the description built from shape and size
  only.
- **Silence is not approval.** Only a classification the operator marked
  right becomes a few-shot example; a proposed memory waits for the same
  mark in `memory_candidates`.
- **Node 0's transcript build respects a token budget**
  (`context.extraction_budget_tokens`, chars/4; unset means none), dropping
  the oldest messages first and never summarising one; two ineffective
  passes put a task on cooldown. A field already in `known` drops out of
  the extractor's schema.

## Repo conventions

- `pytest` + `pytest-asyncio`, `asyncio_mode = "auto"`.
- No linter or formatter is configured; one added is wired through `uv`.
- **Install facts in `config.yaml`**, version-controlled; `.env` holds
  secrets only; knobs are constants in code. `data/`, `*.db*` and `.env` are never committed.
- **The Discord user token must never reach logs, tracebacks or the task
  DB.** `friday/kernel/ops/redact.py` scrubs on the way out, including from
  `sys.excepthook` and `threading.excepthook`; a node's exception text is
  scrubbed in `DAGRunner._invoke` and again where `node_runs.reason` and
  `dag_state.paused_question` are written. Redaction is **by pattern and by
  value** (`friday/sdk/redact.py`, DESIGN-v2 §12): the composition root
  registers the exact secrets the deployment holds — agent API keys, an MCP
  server's declared env and auth secret, the Discord tokens
  (`config.declared_secrets`) — so a key matching no known shape is still
  scrubbed. Value-based redaction of declared secrets is the enforced control;
  the pattern list is the convention that catches the rest.
- **A rule worth stating is worth a test.** Most constraints above are
  enforced by a `grep`/`ast` test, because the ones only written down
  drifted.
- Explicit `__init__.py` packages. A node is testable through its
  declaration. Triage has a fixture set of real messages with expected
  labels — the regression net for prompt changes.

## The api_issue decisions, D1–D14

The board `read-it-the-way-the-operator-does` numbered fourteen decisions
about how an API issue is investigated. They are stated once, in that
board's `spec.md`, with the measurements behind them; this is the index and
what became of each, because a summary that restates them is a second copy
to keep in step.

| | Decision | Where it lives now |
| --- | --- | --- |
| **D1** | Environment from the domain, by rule | **Amended 2026-09-21 — a *row*, not code.** `environment_of` reads `environment` memory rows by longest suffix; `plugins/backend/graph/resolve.py`. Friday serves more rooms than one company's, and a module naming `aperogroup.ai` is an install compiled in |
| **D2** | Findability, not the curl | The curl, **or** the endpoint plus one id. `ApiIssueParams._RULES["_traceable"]`, as a `OneOf` with a group |
| **D3** | Routing is knowledge the operator writes | `route → service → project` rows; a missing row hands over rather than guessing |
| **D4** | Two ways to read a log, and only two | `plugins/backend/sources/logs.py`: `LokiSource` for production, `SshKubectlSource` for dev. Neither knows what it is read for |
| **D5** | Search order, and a bounded window | The window is measured back from the **reporter's message**, not from now; one automatic widening. `plugins/backend/graph/logs.py`. Amended by measurement: the needle is pushed into the back end, because `limit` is a tail |
| **D6** | Friday reads code and never writes it | Every tool a graph is given is a read, enforced by the absence of a verb in `plugins/backend/sources/` rather than by instruction. Decided 2026-09-28, not built: the verb-free place becomes `plugins/backend/toolsets/` (`domains-plug-in` ticket 09) |
| **D7** | Read the version that is running | `ReleaseSource.running_tag` and the comparison in the code node — identical, different, or unresolved, and it says which |
| **D8** | Diagnosis answers a shape | `Diagnosis`, with `refs` as **line pointers, not quotes** (measured: 32/40 quoting against 20/20 pointing), and `alternatives_rejected` required when `conclusive` |
| **D9** | Dependencies between services are knowledge | Half structured, half prose. The structured half is typed in; the prose half is ticket 07 and is the operator's |
| **D10** | Three outputs, two of them approved | **One** is approved now, on the operator's call (2026-09-22): the acknowledgement is sent unread because it answers nothing, the operator's finding is a DM, and only the reporter's copy waits |
| **D11** | No redaction while the agent thinks | `scrub` runs on what goes out and on what the board renders, never on the prompt or the report file |
| **D12** | Findings are written by the agent, directly | Not built. The report file is, and it is the only output that carries the whole of a run |
| **D13** | Time is a knob | **Amended 2026-09-28 — no time budget.** `devops.timeout_seconds` and the node clocks are deleted; each tool call carries `TOOL_CALL_TIMEOUT_SECONDS` (board `domains-plug-in`, ticket 17) |
| **D14** | Friday runs on the operator's machine | With what that machine has — `ssh dev`, the clones under `~/Documents/Apero/`, the MCP sessions they sign in to |

**Two are amended by measurement rather than by argument**, which is the
distinction worth keeping: D1 became a row because a survey found a domain
this rule would have got wrong, and D5's search order changed because a
400-line read of a 35-minute window turned out to cover 84 seconds of it.

# Reasoning

The sections below record why the system took its shape. Kept rather than
rewritten because the argument is still worth reading even where the
conclusion moved; three reversals are marked inline — the "agentic nodes"
vocabulary, triage's tool parameters, and the memory staging tier.

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
> Only some nodes call a model at all. See § Load-bearing constraints
> above and `CONTEXT.md` § Graph.

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
| `tasks` | → conversation | work items and their state |
| `outbox` | → task | outbound intents: conversation, text, sender, reply_to, kind, attempts, last_error, and each row's own approval (`approved_at`, `approved_by`; a card's `approves` names the row it asks about) |
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

**Pydantic AI** (`pydantic-ai-slim`), driven through the **Chat Completions**
API rather than Responses. Chat Completions is the de-facto standard that other
providers implement, so `base_url`, `api_key` and `model` are configuration —
DeepSeek, MiniMax or anything else OpenAI-compatible can be swapped in without
touching code.

```python
client = AsyncOpenAI(base_url=..., api_key=..., max_retries=0)
OpenAIChatModel(model, provider=OpenAIProvider(openai_client=client))
```

**Switching vendor is three config values** — `base_url`, `api_key`, `model` —
because the first version's providers (OpenAI, MiniMax, DeepSeek) all speak Chat
Completions. A `provider: openai | minimax | deepseek` shorthand fills the known
`base_url` (`config.PROVIDER_BASE_URLS`), so a block names the provider instead
of pasting a URL; an explicit `base_url` still wins, for a custom endpoint or a
vendor not listed. Nothing in `harness.py` changes: the seam is the harness
itself, and a non-OpenAI-compatible vendor would be a different Pydantic AI
model class in `_chat_model`, an adapter rather than a rewrite.

**No telemetry is emitted** unless an agent is instrumented, and Friday never
instruments one — the openai-agents predecessor exported traces to OpenAI using
the same key as model requests, which with a third-party provider leaked both
the traffic and the credential, and had to be switched off explicitly.

**A known compatibility risk:** a `json_schema` structured-output mode sends
`response_format: json_schema`
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
the arguments are validated *in this process* and a malformed call handed back
for one correction.

That is the mechanism in `friday/kernel/harness/structured.py` and
`Harness.run_structured`: the shape is a dataclass carried by a Pydantic AI
`ToolOutput` whose function receives the model's raw arguments and validates
them with `fits` (not the framework's own pydantic validation, whose retry
message would quote the offending value — an extractor's arguments are
reporter-controlled text). The shape is described to the model in the prompt,
the answer is validated here, and one that does not fit earns exactly one
correction turn (`retries={'output': 1}`). A reply the model writes as prose
anyway is still read, off the run's captured messages (D13). Nothing is sent on
the wire to enforce the shape, because sending it buys nothing here and costs a
false sense of safety.

## Triage

One model call per mention. It decides three things and performs no I/O:

- **type** — `backend.trace_problem | ops.request_permission | backend.answer_question | skip`
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
> with a different failure mode; it belongs to `friday/kernel/extraction/`, one
> extractor per task type. A test now fails if a triage tool asks for anything
> but `confidence`, so the schemas written above are the ones the code forbids.

Triage's answer is a Pydantic AI **output tool** the framework forces (no text
output is allowed), so the run ends the moment the model calls it — a single
turn with no loop, and no way for the model to answer in prose instead.

**The tool reports the decision; it does not act on it.** Triage stays pure, so
it is testable with no database — the caller applies the outcome.

**Parameters matter more than the type.** The most common real action is not
diagnosis, it is noticing a report is incomplete and asking for what is missing:

> *"Which environment are you using? Could you give me the CURL, or the
> endpoint plus one id you called it with?"*

That is mechanical, high-frequency, and cannot be embarrassingly wrong. An
`ops.request_permission` missing its fields takes that path.

> **Superseded for `api_issue`** (board `build-the-loop`, 2026-09-27). The
> paragraph below describes the extractor-era gate — "an `api_issue` with no
> `curl` and no `endpoint`+`identifier` takes the ask path; one with either goes
> to tracing." `api_issue` no longer runs the extractor (`Intake` replaced it and
> makes no model call), and findability is no longer a precondition: the diagnose
> loop reads log, code and docs and calls `ask_reporter` only when genuinely
> stuck. The historical reasoning is kept for the record (see ADR 0002).

An `api_issue` with no `curl` and no `endpoint`+`identifier` pair took that
path; one with either went to tracing. Same type, different action, decided
by parameters.

**Not the correlationId** (board `read-it-the-way-the-operator-does`, ticket
01). The rule read "a correlationId or a curl" until 2026-09-22, and both
halves of that were wrong: the operator never receives an id from a
reporter — they read it out of the *response* the reporter pastes, which is
why `response` is a field and `correlation_id` is filled from it and never
asked for — and a reporter who wrote "login API, deviceId X, 500" has
already named a request the log can be searched for. The endpoint alone does
not count: it matches every caller of it.

**Never-drop is enforced in `Harness._settle`**, which turns every failure —
a provider exception, a timeout, a turn cap (`UsageLimitExceeded`), a
structured answer that never fit (`UnexpectedModelBehavior`) — into `None` and
a scrubbed `last_error` rather than an exception a caller must catch. Each maps
to a task needing human input.

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

One workflow per type, **deterministic Python** for the wiring — branching, not
reasoning — with the reasoning confined to the one node that calls a model.
`api_issue` is now an agentic loop (board `build-the-loop`, 2026-09-27):

```
api_issue:
    intake      deterministic: placement from environment/service rows,
                regex hints, retrieved memory/skills, the reporter's raw text
    acknowledge tell the reporter it is being looked at (unapproved)
    diagnose    agentic loop: read the log / code / docs with tools, then →
                  Diagnosis  → report
                  Ask        (ask_reporter) → pause for the reporter
                  HandOver   (hand_over)    → the operator
    report      the diagnosis as a Reply that waits for approval
```

There is no upfront "ask for a curl" gate and no "not ours → hand over having
read nothing": the loop reads code and docs, not only logs, so it investigates
what it can and `Ask`s only when genuinely stuck (CONTEXT.md § Action; ADR 0002).
Other task types stay a single deterministic node-0 (ask or hand over); an
agentic loop is taken per type once it earns it.

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
`friday/kernel/tools/memory.py` for the shape and § Memory under Load-bearing
constraints for what is wired.

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
reply row the card names. Buttons are an application-only Discord feature — a user account cannot
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
  The sender's query reads the row: `WHERE outbox.kind <> 'reply' OR
  outbox.approved_at IS NOT NULL`. Approval is a fact about the row, not the
  task — on the task it was written once and never cleared, so a second reply
  on an approved task went out unread (board
  `read-it-the-way-the-operator-does`, ticket 12). The guard is still one
  query rather than a check each caller must remember.
- **`kind` decides whether approval is needed** — see CONTEXT.md. Asking for a
  missing correlationId is the system completing a task's own required
  parameters, not the agent speaking for the operator, so it does not queue
  behind a human.
- **Every delivery is a DBOS workflow** (ticket 07), keyed `outbox-{row}-{attempt}`
  so DBOS gives it exactly-once. The delivery decision — state guard, frozen-hash
  check, staleness check, `dispatching` before the call, send, record after — is
  `Outbox.deliver_once`, written to be safe to re-run; the loop polls
  `sendable_outbound` and hands each row to the durable step through
  `adapter.deliver_outbound`. In-process (tests) the same `deliver_once` runs
  directly, no engine. `friday/kernel/dag/adapter.py` is still the one module that
  names DBOS.
- **A crash mid-send no longer double-posts or silently loses a reply.**
  `dispatching` is written before the channel call, so a send interrupted
  between the call and the record leaves that marker. On resume DBOS re-enters
  the step and `deliver_once` reads the marker: on a channel that dedupes (it
  takes an idempotency key, `outbox-{row}`) it re-sends safely; on one that
  cannot, the outcome is unknown, so the row goes `delivery_unknown` and its task
  to the operator — **never an automatic retry**. No channel supports an
  idempotency key today, so an interrupted real send always lands in
  `delivery_unknown`; the key path is exercised by tests.
- **Approval freezes the payload as a hash.** `approved_payload_hash` is set when
  the row becomes sendable — at enqueue for a policy-approved kind (`approved_by
  = policy`), at approval for a reply — and recomputed at dispatch. A text edited
  after approval no longer matches, so the approval is void and the row goes to a
  person. The staleness check (the conversation moved past what an answer
  answers) still runs alongside it.
- **Retries are bounded**, with backoff, both configured in `config.yaml`. An
  ordinary send failure (the channel raised) returns the row to `queued` with
  its count bumped; on exhaustion the row goes `failed` and its task to
  `needs_human`. This is a clean failure, distinct from the crash mid-call above.
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
reading the JSON API in `friday/kernel/ops/api.py`. The original text follows, with
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

**No longer true: "read-only".** One thing is writable: the operator's own
memory rows (`origin=admin` — facts, constraints, runbooks, services, people),
through the Rooms screen's memory form. It began as a channel's context
`overrides`, the section of `context/<channel>.yaml` the machine never
touched; the YAML files went (board `read-it-the-way-the-operator-does`,
ticket 10) and every memory is a row. That reverses this document's own
premise, and the argument is D7 on the `a-window-on-the-whole-path` board: the
rule exists so that *decisions* have one home, and context is not a decision. It is also why
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
