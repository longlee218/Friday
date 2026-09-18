# Own DAG engine, or Temporal?

Research note for board `read-it-the-way-the-operator-does`. Written
2026-09-17; every URL below was fetched that day unless a line says
otherwise. Versions are what `uv pip install --dry-run` resolved against this
repo's lock on that date. "Judgment" marks an estimate, not a measurement.

The question: keep `friday/dag/engine.py` (307 lines, plus 130 of `state.py`)
or adopt a workflow library — the operator asked about Temporal — for the
investigation graph the spec describes.

## The constraints the answer has to fit

From `CLAUDE.md` and the spec, checked against the code on 2026-09-17:

- **One process, one container.** Every loop is an asyncio task in one event
  loop, because SQLite has one writer. Now one process on the operator's
  macOS machine (spec D14), which makes a second server process *more*
  costly, not less: it is one more thing to keep alive on a laptop.
- **SQLite is the only state store**, and access must be async.
- **Few dependencies**; `openai-agents` is temporary and only
  `friday/agent/harness.py` imports it. The harness owns the clock, the
  retry list, `max_retries=0` on the client, and the recording sink for
  every model call — "the only arrangement in which the record and the
  invoice agree".
- **The engine today**: one `Action` per run; checkpoint after each node
  keyed by a fingerprint of the parameters, so a reporter's answer discards
  the state and re-runs from node 1; no clock (nodes bound themselves and
  return `{"timed_out": true}`); results must survive JSON or the node
  re-runs; `_resume_point` walks forward from the entry so a state written
  by an older graph still resumes somewhere sensible. The engine's own
  docstring records the last time this was measured: "twenty-two extra
  packages, a second HTTP client, and two foreign tables in the one SQLite
  file, for something that fits in this file."

## Comparison table

| Option | One process? | SQLite? | Packages added to this venv | Checkpoint model | Human-in-the-loop | Timers / retries | Versioning | Maintenance (latest, date, licence) |
|---|---|---|---|---|---|---|---|---|
| **Keep `friday/dag`** | yes | yes, one table (`dag_progress`) | 0 | per node, keyed by params fingerprint | return `Ask`/`HandOver`; re-run on new message | none in engine; nodes self-bound | walk-forward resume tolerates changed graphs | in-repo, 49 + 32 tests |
| **Temporal** (temporalio 1.33.0) | **no** — Go server + Python worker | **no** — server has its own DB (SQLite file in dev mode only) | 3 (SDK) / 12 with the OpenAI plugin, which **downgrades** `openai-agents` 0.22→0.20 and `openai` 3.6→2.54 | event history replay; activities for all I/O | signals, updates, `wait_condition` | durable `asyncio.sleep`; activity retry default ∞ attempts | `patched()` / worker build IDs | 2026-09-15, MIT |
| **DBOS Transact** (dbos 3.0.0) | yes (library + its own thread pool) | yes, default; 12 system tables in a `dbos` schema, own file by default; Postgres "recommended for production" | 3 (`psycopg` unconditional) | step results logged; workflow re-executed deterministically | `DBOS.recv`/`send`, `set_event`/`get_event` | durable `DBOS.sleep`; step retries off by default, 3 attempts ×2 | app version (docs page 404 — not verified) | 2026-09-16 (major, breaking), MIT |
| **LangGraph** (1.2.11 + checkpoint-sqlite 3.1.1) | yes | yes via `AsyncSqliteSaver` — docs: "not recommended for production workloads" | **19** incl. `langchain-core`, `langsmith`, `httpx`, `orjson`, `zstandard` | per superstep; node re-executes from its start on resume | `interrupt()` + `Command(resume=)` | no durable timer; per-node `RetryPolicy`, `TimeoutPolicy` | none found (not verified) | 2026-08-11, MIT |
| **Burr** (apache-burr 0.43.0) | yes | yes, `AsyncSQLitePersister` (aiosqlite, "primarily for prototyping") | 1 | full state per action | stop the app, re-run from persisted state | none documented | none | 2026-08-25, Apache-2.0, Apache incubator |
| **Pydantic Graph** (2.43.0) | yes | — | 2 | **V2 removed `pydantic_graph.persistence`** | — | — | — | 2026-09-12, MIT |
| **Prefect 3** (3.8.6) | **no** — API server (ephemeral subprocess at best) | server's own SQLite at `~/.prefect` | **60** | task-run states in server | pause/suspend flow runs (not verified here) | yes | deployments | 2026-09-15, Apache-2.0 |
| **Hatchet** (sdk 1.40.1) | **no** — engine + Postgres | **no** | 10 (grpc) | server-side | durable event waits | durable sleep | server-side | 2026-09-09, MIT |
| **Restate** (sdk 1.0.5) | **no** — Rust server binary | **no** (RocksDB) | 1 | journal replay | awakeables | yes | server-side | 2026-09-02; server BSL 1.1 → Apache-2 after 4 years |
| **Inngest** (0.5.19) | **no** — Go server binary | server's own SQLite | 4 | step memoisation | `waitForEvent` | yes | server-side | 2026-06-23; server SSPL + delayed Apache-2 |
| **Google ADK 2** (2.9.1) | yes | via `DatabaseSessionService` | **17** incl. `google-genai`, `google-auth`; downgrades `opentelemetry-api`, `websockets` | node checkpoints in graph workflows | `RequestInput`, `rerun_on_resume` | not verified | — | 2026-09-15, Apache-2.0 |

Package counts: `uv pip install --dry-run <pkg>` inside this repo's `.venv`,
2026-09-17. Dates and licences: `https://pypi.org/pypi/<pkg>/json`, same day.

## 1. What adopting Temporal actually requires

**A server, and it is a second process.** The Python SDK talks gRPC to a
Temporal server. Locally that is `temporal server start-dev`, a Go binary;
by default "Workflow Executions are lost when the server process dies", and
`--db-filename` is the "Path to file for persistent Temporal state store".
The same page: "The development server is not intended for production use.
It skips certain HTTP security checks to make local use simpler."
(https://docs.temporal.io/cli/command-reference/server, fetched 2026-09-17.)
The SDK can launch that binary from Python —
`WorkflowEnvironment.start_local()` "will download the CLI to a temporary
directory" and takes `dev_server_database_filename` — but it lives in
`temporalio.testing` and is documented for tests
(https://python.temporal.io/temporalio.testing.WorkflowEnvironment.html).
So the honest shapes are: a dev server nobody supports in production, or a
production deployment with Postgres/Cassandra that Temporal's own docs point
to, or Temporal Cloud. All three break **one process** and **SQLite only**:
Temporal's state is Temporal's, in its own database, and the checkpoint
this repo shows on the monitor would be a second copy of the truth.

**Determinism and the sandbox.** Workflow code "cannot call functions that
produce non-deterministic results such as direct I/O, system time,
randomness, or threading"; the sandbox "completely reloads
non-standard-library and non-Temporal modules for every Workflow run"
unless they are marked `imports_passed_through()`; the sandbox can be
switched off per workflow, at the cost of the determinism checks
(https://docs.temporal.io/develop/python/python-sdk-sandbox). Every node
in the spec's graph does I/O (kubectl, git, SQLite, a model), so every node
becomes an activity and the graph function becomes pure glue. Activities
and workflows can run in one worker process on one event loop; "if you
have blocking code in an async def function, it blocks your event loop and
the rest of Temporal"
(https://docs.temporal.io/develop/python/python-sdk-sync-vs-async) — the
same rule this repo already lives under.

**Human-in-the-loop, timers, retries.** Signals, updates and queries;
`await workflow.wait_condition(lambda: self.approved)`; `asyncio.sleep`
inside a workflow is durable
(https://docs.temporal.io/develop/python/message-passing). Activities retry
by default with initial interval 1s, backoff 2.0, **maximum attempts ∞**;
"Workflow Executions do not retry by default"
(https://docs.temporal.io/encyclopedia/retry-policies). Changing a workflow
with runs in flight needs `patched()` / `deprecate_patch()` or Worker
Versioning, and an unpatched change "is likely to cause a nondeterminism
error" (https://docs.temporal.io/develop/python/versioning).

**The OpenAI Agents SDK integration.** GA since 2026-03-23; as of 1.0.0
(2026-09-16) it is a separate package, `temporalio-openai-agents`
(https://docs.temporal.io/develop/python/integrations/openai-agents;
https://pypi.org/project/temporalio-openai-agents/1.0.0/). The plugin
"intercepts `Runner.run` calls, transforming each model invocation into an
Activity" with `ModelActivityParameters` — `start_to_close_timeout`
default 60s, `retry_policy` — and "Activity-level retry policies take
precedence" over the OpenAI client's own retries. `@function_tool` tools
run inside the workflow and must be deterministic; I/O tools go through
`activity_as_tool()`.

Three collisions with the harness rule, all read off that page against
`harness.py` on 2026-09-17:

1. **The plugin's pin.** Its `pyproject.toml` declares
   `openai-agents>=0.20,<0.21` and `openai>=2.45,<3`
   (https://raw.githubusercontent.com/temporalio/ai-integrations/main/python/openai_agents/pyproject.toml).
   This repo runs `openai-agents` 0.22.0 and `openai` 3.6.0; the dry-run
   install downgrades both. The one dependency the repo calls temporary
   would be pinned two minors back by a second dependency.
2. **Two retry loops, two clocks.** `Harness._attempts` and
   `timeout_seconds / max_attempts` would run *inside* the model activity,
   under an activity retry policy that defaults to unbounded attempts and
   its own 60s timeout. Either the activity retry is set to one attempt
   (then Temporal adds nothing to the call) or the record and the invoice
   disagree again, which is the exact failure `max_retries=0` was set to
   end.
3. **The recording sink is I/O.** `_settle` is where `Runner.run` is called
   and where the call is handed to the sink that writes `model_calls`. The
   plugin runs `Runner.run` in workflow code, where SQLite writes are
   forbidden. The sink would have to move into the activity, i.e. the
   harness would be split across the workflow/activity boundary — the one
   file that may import the SDK becomes two.

None of these is fatal, all three are work, and what they buy is a second
process and a second state store this repo has decided against.

## 2. In-process alternatives that match SQLite

**DBOS Transact (Python 3.0.0, 2026-09-16, MIT).** A library: "By default,
it uses SQLite, which requires no configuration"; "For production use, we
recommend connecting your DBOS application to a Postgres database"
(https://docs.dbos.dev/python/integrating-dbos). Default system database
`sqlite:///<app_name>.sqlite`, a SQLAlchemy engine, a thread pool sized by
`max_executor_threads` (https://docs.dbos.dev/python/reference/configuration).
`psycopg[binary]` is an unconditional dependency; `aiosqlite` is an extra
(https://raw.githubusercontent.com/dbos-inc/dbos-transact-py/main/pyproject.toml).
Async workflows and steps are supported; `DBOS.sleep()` "is durable — DBOS
saves the wakeup time in the database"; workflow code "should invoke the
same steps with the same inputs in the same order", all I/O in steps
(https://docs.dbos.dev/python/tutorials/workflow-tutorial). Step retries
default `retries_allowed=False`, 3 attempts, 1s, ×2; `timeout_seconds`
applies to async steps only
(https://docs.dbos.dev/python/tutorials/step-tutorial). Signals:
`DBOS.recv()`/`DBOS.send()` with a 60s default timeout, `set_event`/
`get_event` (https://docs.dbos.dev/python/tutorials/workflow-communication).
Twelve system tables in a `dbos` schema
(https://docs.dbos.dev/explanations/system-tables) — in SQLite that is a
second file, or twelve foreign tables in `friday.db`. Versioning: the
upgrade-workflow page returned 404 on 2026-09-17; **not verified**. Whether
the async path on SQLite runs through `aiosqlite` or a threaded sync
driver: **not verified**. What it is: the closest fit — in-process,
SQLite, durable sleep, signals — at the cost of a second SQLAlchemy engine
and thread pool over the one-writer database, a major release one day old,
and a Postgres driver compiled in for nothing. The DBOS blog on 2026-06-18
announced SQLite for the Go SDK only; the Python docs above say it is the
Python default too, and I could not find when that landed.

**LangGraph (1.2.11, 2026-08-11, MIT).** `AsyncSqliteSaver` in
`langgraph-checkpoint-sqlite` uses aiosqlite, and its own reference says it
"is not recommended for production workloads due to limitations in SQLite's
write performance"
(https://reference.langchain.com/python/langgraph.checkpoint.sqlite/aio/AsyncSqliteSaver).
`interrupt()` pauses, `Command(resume=)` continues
(https://docs.langchain.com/oss/python/langgraph/interrupts). Per-node
`RetryPolicy` (backoff + jitter), `TimeoutPolicy`, error handlers, from
LangChain's blog of 2026-06-04
(https://www.langchain.com/blog/fault-tolerance-in-langgraph). Durability
modes `exit`/`async`/`sync`; on resume the node re-executes from its start
and side effects must sit in `@task` — the durable-execution page redirected
on every fetch, so that sentence comes via search snippets
(https://reference.langchain.com/python/langgraph/types/Durability). No
durable timer: a run "lives in a single process, so if that process dies
then the run dies with it" — Temporal's words about LangGraph, on Temporal's
blog, 2026-07-16 (https://temporal.io/blog/temporal-langgraph-plugin-durable-execution).
Nineteen packages including `langchain-core`, `langsmith` and a second HTTP
client is the same bill the engine docstring already declined.

**Burr (apache-burr 0.43.0, 2026-08-25, Apache-2.0).** One package. Actions
with declared reads/writes, explicit transitions, `AsyncSQLitePersister` on
aiosqlite that the docs recommend "primarily for prototyping"; each step
stores the full state as JSON (https://burr.dagworks.io/reference/persister/).
No timers, retries or signals documented; human-in-the-loop is "stop and
re-run from the saved state", which is what `friday/dag` already does.
Now an Apache incubator project (https://github.com/apache/burr). It would
replace 437 lines with a dependency that does the same thing, plus a
telemetry UI this repo already has in `web/`.

**Pydantic Graph (2.43.0, 2026-09-12, MIT).** Ruled out by its own changelog:
"The `pydantic_graph.persistence` package and the `pydantic_graph.mermaid`
module are removed", with resumability now a Pydantic AI Harness capability
for *agent runs*, not graphs (https://pydantic.dev/docs/ai/changelog/;
https://github.com/pydantic/pydantic-ai/issues/3697, opened 2025-12-10).
The version `uv` resolves has no checkpoint at all.

**Prefect 3 (3.8.6, 2026-09-15, Apache-2.0).** Sixty packages and an API
server; the ephemeral-subprocess mode is off by default
(`PREFECT_SERVER_ALLOW_EPHEMERAL_MODE`, https://docs.prefect.io/v3/api-ref/settings-ref).
A data-pipeline orchestrator, not an in-process graph.

**Hatchet, Restate, Inngest.** All three are a server: Hatchet on Postgres
(https://github.com/hatchet-dev/hatchet); Restate a single Rust binary on
RocksDB under BSL 1.1 converting to Apache-2 four years per release
(https://github.com/restatedev/restate/blob/main/LICENSE); Inngest a Go
binary with SQLite and in-memory Redis under SSPL with delayed Apache-2
(https://github.com/inngest/inngest; https://www.inngest.com/docs/self-hosting).
Each breaks "one process" on the first line of its quickstart.

**Google ADK 2 (2.9.1, 2026-09-15, Apache-2.0).** ADK 2.0 has graph
workflows with node checkpoints, `RequestInput` for a human pause and
`rerun_on_resume` (https://adk.dev/graphs/dynamic/). It is an agent
framework with its own model layer and seventeen packages including
`google-genai`; adopting its graph means adopting its harness, which is the
decision `friday/agent/harness.py` exists to keep in one file.

## 3. What the engine lacks, and what each gap costs

Judgment throughout, against `engine.py` on 2026-09-17.

| Gap | Library gives | Engine today | Cost to add (judgment) | Worth it? |
|---|---|---|---|---|
| Per-node timeout | Temporal `start_to_close_timeout`; LangGraph `TimeoutPolicy`; DBOS step `timeout_seconds` | none; every node wraps itself and declares `timed_out → finish` first (spec) | ~25 lines: `Node(timeout_seconds=)`, `asyncio.wait_for` in `run()`, record `{"timed_out": true}` rather than raise | **yes** — the spec's workaround is the "seven call sites remembering a keyword" shape the harness already refused |
| Per-node retry with backoff | Temporal activity policy; LangGraph `RetryPolicy`; DBOS step retries | none; model calls are retried by the harness | ~30 lines: `Node(retry=Retry(attempts, on=(...)))`, explicit exception tuple, doubling wait; plus tests | yes, for kubectl/git nodes, with the harness's rule: an explicit list, never a guess from the message |
| Durable timer / sleep | Temporal `asyncio.sleep`; DBOS `DBOS.sleep` | none, and no node needs one: the wait for a reporter is a re-run on the next message | ~50 lines: `wake_at` column on `dag_progress`, the pool's poll loop selecting due rows | not until a ticket names a timer ("nudge after 24h") |
| Signals | Temporal signals; DBOS `recv`; LangGraph `Command(resume=)` | the only signal is "the reporter replied", carried by the fingerprint: new params, new state, re-run from node 1 | ~50 lines for a general one (a row, a `waiting_on` key, the pool loop) | not until a second kind of external event exists |
| Fan-out with join | all of them | `asyncio.gather` inside a node, by design | ~100 lines: multiple live edges, a join node, checkpoint semantics for partial branches | not until a graph has two independent branches; the spec's is linear with conditions |
| Versioning | Temporal `patched()`; DBOS app version | walk-forward resume plus the fingerprint already tolerate a changed graph; an unknown key is never read | ~10 lines: a `dag_version` in the checkpoint key so a renamed node cannot inherit a stale result | yes, cheap insurance |
| Run history / UI | Temporal UI; Burr UI; LangSmith | `dag_progress` row, `trail`, `paused_at_node`, `/api/tasks/{id}`, SSE on the monitor | 0 | already there, and a second UI would render prompts that never pass `redact.scrub` — the two-renderers failure `CLAUDE.md` records twice |

Total for the three "yes" rows: roughly 65 lines of engine plus tests, well
inside the file's own "revisit when durable resume spreads past two
workflows".

## 4. Experience reports

- **Grid Dynamics** (on Temporal's blog, 2025-09-29): a LangGraph research
  agent keeping state in Redis, "thousands of lines of custom
  error-handling code", Kafka for exactly-once; moved to Temporal for state
  as a workflow variable, declarative retries, and scaling by worker
  replica count (https://temporal.io/blog/prototype-to-prod-ready-agentic-ai-grid-dynamics).
  Every pain named is multi-worker, multi-service. Vendor.
- **Manetu** (guest post on Temporal's blog, 2026-09-03): maps LangGraph
  threads to Temporal Cloud workflows without changing agent code, for
  resilience and credential brokering
  (https://temporal.io/blog/manetu-the-thread-is-the-workflow). Vendor
  venue, Temporal Cloud.
- **Monk** (Temporal blog): Inngest → Temporal, one workflow at a time
  behind flags (https://temporal.io/blog/how-monk-migrated-100-workflows-inngest-to-temporal).
  Vendor. **Atlan**: Argo → Temporal (https://blog.atlan.com/engineering/argo-to-temporal-migration/).
- **LangChain's own comparison** (2026-06-06): Temporal for "deterministic
  distributed system workflows", LangGraph for agents; many teams "run both"
  (https://www.langchain.com/resources/langgraph-vs-temporal). Vendor.
- **Pedro Alonso** (2026-07-01, independent): ~200 lines of stdlib Python on
  SQLite gives step replay, per-step backoff and human-approval signals;
  "graduate to Temporal" for multiple worker machines, multi-day durable
  timers, large fan-out with signals and cancellation, or an operations UI —
  "for architecture, not throughput"
  (https://www.pedroalonso.net/blog/durable-llm-workflows-sqlite/).
- **Gunnar Morling** (2025-11-20, independent): a sub-1,000-line Java
  proof of concept on SQLite; SQLite "for self-contained systems like
  individual AI agents", DBOS when Postgres already holds your data,
  Temporal/Restate for multi-service
  (https://www.morling.dev/blog/building-durable-execution-engine-with-sqlite/).
- The Hacker News thread on the SQLite-workflows post (around June 2026)
  splits on whether "production" means one machine or many; the
  pro-library side's concrete wins are versioning and a UI
  (https://news.ycombinator.com/item?id=48326802).

I found no published report of a team moving *from* Temporal or LangGraph
*to* a hand-written graph; the independent posts above argue for not
adopting in the first place rather than for leaving.

## Recommendation for friday

**Keep the engine.** Adopt nothing now. Reconsider DBOS — not Temporal —
the day one of Pedro Alonso's four graduation lines is crossed, and the
only one this project could plausibly cross is a multi-day durable timer.

What each option breaks, by name:

- **Temporal** breaks *one process* (a Go server), *SQLite only* (its own
  store), and *the harness owns the call* (retry policy above the harness's,
  `Runner.run` moved into sandboxed workflow code away from its sink). Its
  agents plugin pins `openai-agents` to 0.20.x against this repo's 0.22.0.
  It is built for a fleet of workers; this is one laptop.
- **DBOS** keeps one process and SQLite but adds a second SQLAlchemy engine,
  a thread pool, twelve tables or a second file, and a compiled Postgres
  driver, for a major version released yesterday. The best "later" option.
- **LangGraph** breaks *few dependencies* (19 packages, a second HTTP
  client) and its own SQLite saver says not for production.
- **Burr** duplicates the engine for one package and nothing new.
- **Pydantic Graph** has no persistence in the version that installs.
- **Prefect, Hatchet, Restate, Inngest, ADK** each need a server or a
  second agent framework.

What to add to `friday/dag/engine.py`, in order:

1. **Per-node timeout** (~25 lines). Move the spec's "each node bounds
   itself" into the runner: `Node(timeout_seconds=)`, `wait_for`, record
   `{"timed_out": true}` on expiry, so `timed_out → finish` stays an edge
   but no node has to remember the wrapper.
2. **Per-node retry** (~30 lines). `Node(retry=)` with an explicit exception
   tuple and doubling backoff, mirroring `Harness._attempts`, for the
   kubectl and git nodes. Model calls stay the harness's.
3. **Graph version in the checkpoint key** (~10 lines). A `dag_version` on
   the `DAG` and in `dag_progress`, so a renamed or reordered node cannot
   inherit an old node's result.
4. **`wake_at` on `dag_progress`** (~50 lines) — only when a ticket asks for
   a timer. Nothing on the current board does.
5. **Fan-out/join** (~100 lines) — only when a graph has two independent
   branches. `asyncio.gather` inside a node covers everything the spec draws.

The trigger to revisit the whole question is not a feature; it is a second
machine running nodes, which is the one thing every library above is built
for and this repo has decided against.
