# Spec: read it the way the operator does

Status: written 2026-09-16 from a grilling session with the operator, after
the question "what do you actually do when an API issue lands" was asked for
the first time. Every decision below is theirs; the facts were measured in
the session and say so. Nothing is built yet. Tickets 01–06 and 08 are
`ready-for-agent`; 07 is `ready-for-human`, because the knowledge it asks
for is knowledge only the operator has.

## Why this board exists

There was a five-node `api_issue` graph — read the logs, find the code,
analyse, propose a patch, compose a reply — and the operator removed it
(commit de315e7): a workflow nobody had described, invented from a guess at
what investigating an API fault looks like, and every node of it skipped on
every run because no tool server was ever configured. `register_dags`'s
docstring still carries the rule that came out of it: build a multi-node
graph when there are steps worth skipping **and somebody has said what they
are**.

This board is the somebody saying. The operator was interviewed one question
at a time about a real case, and the graph below is a transcription of their
own routine, with the parts they said Friday may not do left out.

## What the operator does today

Given a curl from a reporter:

1. **Read the domain** to know the environment. `*.dev.aperogroup.ai` is
   dev; `aperogroup.ai` or `apero.vn` without `dev` is production; any other
   domain is a third-party service, not ours. There is no staging anywhere.
2. **Find the pod.** Domain → pod name is not derivable — `api-reelme-v2.dev.
   aperogroup.ai` is served by `backend-reelme-v2-<hash>` because the devops
   team named it so. The operator SSHes in and greps. They asked for this
   mapping to live in the channel's memory, since each channel is one product
   plus the services around it.
3. **Find the request in the log.** By correlationId if the reporter pasted
   the *response* (that is where it comes from — reporters do not know the
   word, they have the response); otherwise approximately, by path and
   timestamp; failing that, they replay the request themselves.
4. **Read the error.** A stack trace names the cause. A business error —
   200 with wrong data, 4xx with a domain message — needs reproduction, a
   look at the database, and the code's logic, and is much harder.
5. **Reply "đang check"**, fix locally, push to dev. On production the same,
   through the devops MCP for logs instead of SSH.

## What Friday does instead (the decisions)

**D1 — Environment from the domain, by rule, in code.** Three outcomes:
`dev`, `production`, `external`. `staging` leaves `ApiIssueParams`' enum:
no project has one. `external` ends the graph at once with a hand-over
naming the domain and promising nothing — sometimes an unknown domain is a
proxy of ours the table does not know yet, and that call is the operator's.

**D2 — Findability, not the curl, is what a task needs.** A curl is the
common case but not required: "em vừa call API login với deviceId này nhưng
đang gặp lỗi 500" is enough, because the log carries `deviceId`, `userId`,
`path`. The rule becomes: a curl, *or* an endpoint plus one identifier the
log line carries. Without a curl there is no domain, so the graph asks for
the environment. The reporter is asked for **the response**, never "the
correlationId".

**D3 — Routing is channel knowledge the operator writes, structured, and
code looks it up.** Per channel: domain → `{env, cluster, namespace, app}`
for production (Loki labels), domain → pod name pattern for dev, app → repo
path on this machine. A missing row is a hand-over, not a guess. Tested for
shape.

**D4 — Two ways to read a log, and only two.** Production through the
devops MCP's Loki tools, which the session measured: every line is JSON with
`correlationId`, `method`, `path`, `statusCode`, `userId`, `deviceId`,
`errorCode`, and an `ExceptionFilter` line carries the stack with file:line.
Dev through `kubectl logs` against the kubeconfig already on this machine —
dev is **not** in that Loki (seven days of `backend-reelme-v2` streams are
all `oregon-llm`/`vsl`, and the dev replicaset hash appears nowhere), and
the devops team cannot be asked to ship it. Every replica matching the
pattern is read.

**D5 — Search order, and a bounded window.** correlationId from the response
first; then path plus identifier within a window of six hours back from the
reporter's message. Not found: ask the reporter once for the response and the
time of the call (through the outbox, approved), resume on their answer;
still nothing, hand over with what was looked at, the window included.

**D6 — Friday reads code and never writes it.** Read-only, from the repo on
this machine: the file and line the stack names, then CodeGraph
(`codegraph explore` with the project's own path — every backend repo under
`~/Documents/Apero/` is indexed) to follow the call, and the repo's own
`docs/` and `CLAUDE.md` read in place rather than copied into Friday.
Friday **modifies no code, replays no request, pushes nothing, deploys
nothing.** Every tool it is given is a read.

**D7 — Read the version that is running.** On production the graph reads
the pod's image version through the MCP's read tools and reads code at that
tag — in a detached `git worktree` under `data/checkouts/<repo>@<tag>`, never
by checking out inside the operator's working tree, with CodeGraph indexed
there. Dev reads the main clone.

**D8 — Diagnosis answers a shape, and `conclusive` is a field of it.**
Cause, evidence (verbatim log lines), code path, suggested fix, confidence,
and *what was not checked*. Escalation to the database happens only when
all three hold: the model says it is not conclusive, the log carries no
stack for that request, and the channel's dependency knowledge names a
table and a key to look at. Any one alone is not enough — a model alone
says "unsure" to earn another tool call; a rule alone reads a downstream
service's stack ("Already have transaction" is Midas speaking through
ReelMe's exception filter) as a local cause.

**D9 — Dependencies between services are knowledge, half structured.**
correlationId does **not** cross services (measured: an id from
`/v1/midas/intent` appears in `backend-reelme-v2` and nowhere else in the
namespace), so "order calls Midas; check Midas first, then the
`transactions` state" cannot be derived. The identifying half — which Loki
app is Midas, which key joins the two logs, which repo — is structured for
code to look up; the reasoning half — check what first, what a code means —
is prose the model reads. Knowledge about a shared service lives once and
channels point at it. The operator writes both; ticket 07.

**D10 — Three outputs, two of them approved.** At task open, a fixed
template "đang xử lý" is queued for the reporter and waits for approval.
At the end, a report file at `data/reports/<task_id>.md` — its sections come
from a skill the operator loads, the graph only reserves the slot — the bot
DMs the operator a three-line summary plus the path, and a plain-language
brief of the cause is queued for the reporter, approved. The graph does not
wait for the operator to fix anything: "after it is handled" is the moment
they approve the brief.

**D11 — No redaction while the agent thinks.** The model and the report see
the log as it is, emails and user ids included; `scrub` applies to what
leaves for Discord and what the board renders, as it already does.

**D12 — Findings are written by the agent, directly.** The diagnosis agent
gets the memory tools and writes `finding` rows scoped to the channel, so
the next case with the same `errorCode` starts from the last conclusion.
Not `memory_propose`: the operator chose the direct write, and the
instruction-shape guard still stands at the write path.

**D13 — Time is a knob.** The graph's ceiling and each node's live in
`config.yaml`; the default is five minutes for the whole run. A node that
runs out still writes the report with what it has, and "not checked" says
where it stopped. `daily_token_budget` stays unset until a week has been
measured, the way every other agent here started.

**D14 — Friday runs on the operator's machine, with what the machine has.**
Kubeconfig, the repos, the MCP session the operator authenticated. This is a
reversal of `CLAUDE.md`'s "running it on a server" section, which ticket 08
corrects. It is also why D6's "every tool is a read" matters more, not less:
the credentials in reach are the operator's own.

## Facts measured in the session, so nobody re-derives them

- MCP `devops-generic` at `devops-generic-mcp.aperogroup.ai`, Keycloak realm
  `apero-headquarter`, per-user; groups `dev-backend`, `dev-backend-lead`.
- Read tools used and safe: `whoami`, `k8s_list_clusters`, `k8s_list_pods`,
  `loki_labels`, `loki_label_values`, `loki_series`, `loki_pod_logs`,
  `loki_query_range`, `whatis`. `release_*` and `vibecode_*` are writes and
  are never given to Friday.
- Loki labels: `apero_cluster`, `namespace`, `app`, `pod`, `container`, …;
  clusters `byteplus`, `oregon-l40s`, `oregon-llm`, `virginia-l40s`,
  `vultr-ailab`; namespaces `sw`, `vsl`, `vibecode`, `iam`, ….
- `loki_series` over `{app=~"backend-reelme.*"}` for seven days is ~780 KB;
  never ask it unbounded from a graph.
- `BE-Midas` is not registered with the MCP under that name; which Loki app
  is Midas is a row in ticket 07's table.
- `sensitive_words` does not match `Authorization: Bearer …`, so a curl
  reaches the model.
- `db-generic` at `devops-dbx.aperogroup.ai` exists and was not
  authenticated in the session; its tools and whether it is read-only are
  ticket 05's first fact to fetch.

## The graph at engine level

Read against `friday/dag/engine.py`, `state.py`, `prepare.py` and
`tasks/pool.py` on 2026-09-16. Three mechanics shape the nodes and are not
negotiable without changing the engine:

- **One `Action` per run.** `Pool._outcome` reads the last `Ask`/`Reply`/
  `HandOver` on the trail and that is the run's answer. So "đang xử lý"
  cannot be *returned* mid-graph — a node that returns a `Reply` is the end.
  The `notify` node writes its two outbox rows itself through `deps.db`
  (`Kind.REPLY` + `Kind.APPROVAL_CARD`, the same pair `_propose` writes),
  returns a plain dict, and is **idempotent** on `outbound_count`, because
  the graph re-runs from node 1 whenever the parameters change.
- **A reporter's answer discards the state.** Node 0 runs every pass outside
  the checkpoint; the checkpoint is keyed by a fingerprint of the
  parameters, and the reporter's response changes them. So D5's "resume on
  their answer" is a re-run from `route`, not a resume at `logs`. Cheap
  nodes re-run; `notify` skips itself; `logs` runs again with the new key,
  which is the point. There is no cycle: `diagnose` cannot run twice, so the
  post-database pass is its own node, `conclude`.
- **`Ask` with `auto_ask` off is a hand-over**, and `auto_ask` is off since
  bec6f1e. The "send me the response" question therefore goes out as a
  `Reply` — template text, waits at the approval gate, task in `review` —
  not as an `Ask`.

And two the engine does not do, which the nodes must:

- **Results must survive JSON** or the node re-runs on resume. Every node
  returns dicts, lists and strings; `Diagnosis` is stored as `asdict`.
- **The engine has no clock.** A `wait_for` around `runner.run()` would
  raise, and `_walk` turns a raise into `HandOver("failed")` — losing the
  partial report D13 promises. Each node bounds *itself* and returns
  `{"timed_out": true, ...partial}`; an edge `timed_out → finish` is declared
  first on every node, because `next_after` takes the first edge whose
  predicate holds. The five-minute ceiling is validated at config load as
  the sum of the node ceilings, not enforced by a timer.

Nodes and state keys, edge predicates over `state` only (`Edge.when` is not
handed `deps`):

    prepare   ApiIssueParams (node 0)         notify    {outbound_id|skipped}
    route     {env, cluster, namespace, app}  logs_prod {found, lines, stack, query, window}
              | {env, pod_pattern} | HandOver logs_dev  same shape
    code      {commit, checkout, frames[], docs[]}
    diagnose  asdict(Diagnosis)               db        {queries[], rows[]}
    conclude  asdict(Diagnosis)               finish    Reply(brief)  (+ report file, DM row)

    prepare → route
    route → notify            when route is not HandOver
    notify → logs_prod        when route.env == "production"
    notify → logs_dev         when route.env == "dev"
    logs_* → finish           when timed_out
    logs_* → code             when found
    (logs_* returning Reply/HandOver: no edge matches; run ends)
    code → finish             when timed_out
    code → diagnose
    diagnose → db             when not conclusive and stack is None and dependency names table+key
    diagnose → finish
    db → conclude
    conclude → finish

## Revision 2026-09-17: a straight line of investigators

The operator rejected the branching shape above: "không nên chia và rẽ
nhánh nhiều như vậy", and no refusal on domain — a domain the table does not
know is still investigated, as far as the evidence allows. What replaces it:

**Edges do not branch; nodes decide whether they have work.** The graph is a
line. Every investigator implements one contract and skips itself with a
reason when it does not apply:

    class Investigator:                       # layer 2, knows no graph
        kind: str                             # "log", "code", "database", ...
        def applies(self, state) -> bool | str    # True, or the reason to skip; code, no model
        async def gather(self, state, deps) -> Evidence
        budget_seconds: int                   # from config.yaml; wait_for inside, never raise

    Evidence = {kind, status: found|empty|skipped|timed_out|error, reason,
                items[], source, elapsed_s}   # one JSON-safe shape Diagnose reads

    Prepare → Resolve → Notify → [RecallMemory · CheckDeploy · FetchLog ·
    TraceDependency · SearchCode · FetchMetrics · CheckConfig · InspectDatabase]
    → Diagnose → Explain → Report

Frame nodes, every run: `Prepare` (node 0, unchanged); `Resolve` replaces
`Route` and refuses nobody — env by rule or `unknown`, service, namespace,
repo, dependencies, and an `unresolved[]` list for what the table lacked;
`Notify` (unchanged); `Diagnose`, the one required reasoning node, reads every
`Evidence` and answers `cause, confidence, conclusive, next_checks[],
not_checked[]`; `Explain`, the responder writing the reporter's brief in the
operator's voice — split from `Diagnose` because two readers, two voices;
`Report`, file + DM + the run's one `Reply`.

Investigators, cost-ascending: `RecallMemory` (findings by service and
errorCode), `CheckDeploy` (running version, last rollout, k8s events — and
the tag `SearchCode` reads at), `FetchLog` (**prod and dev in one node**,
`LogSource` chosen by `resolve.env`; `unknown` searches Loki broadly by path
under `limit`), `TraceDependency` (follow the request downstream by the join
key the knowledge names; skips without a dependency row), `SearchCode`
(frame → source, grep, CodeGraph, `docs/`, error-code tables, at the deployed
tag), `FetchMetrics` (Prometheus through the MCP: error rate and latency
around the time — "one user or everyone"), `CheckConfig`
(`k8s_read_configmap`, env vars; skips on `unknown`), `InspectDatabase`
(SELECT by the key the knowledge names).

**D8 amended.** `InspectDatabase.applies` = the knowledge names a table and
a key **and** `FetchLog` found no stack. The third condition — the model
declaring itself inconclusive — is gone, because `Diagnose` now runs after
every investigator and a gate on its answer would be the branch the operator
removed. `Diagnose.not_checked` records what was skipped and why.

**D5 amended.** No node asks the reporter. `Diagnose.next_checks` may name
"the reporter's response"; `Report` then queues the template question as the
run's `Reply` instead of a brief. The answer changes the parameters, the
state is discarded, and the line runs again from `Resolve` with the new key.

Considered and not proposed: `ReplayRequest` (forbidden), `ProposePatch`
(forbidden), `PlanInvestigation` (a model choosing which nodes run — the
graph that was removed).

Reasoning nodes: two, `Diagnose` and `Explain`; with node 0's extractor,
three model calls per run. Investigators call no model.

Open, pending the research files under `research/`: whether the engine stays
hand-written or a workflow library carries this shape, and what the surveyed
agents call these parts.

## Memory: one store, twelve kinds (decided 2026-09-17)

The operator's decision, reversing the three-store split of 2026-09-01:
**the YAML context files go; every memory is a row in SQLite**, entered
through the UI or written by an agent. What the files gave — comments and a
git diff of what the operator declared — is traded for `superseded_by` and
`deleted_at`, which already keep the history of a row, and for one write path
the instruction-shape guard stands at instead of three.

**One table, two shapes.** `memories` gains three columns:

    origin   model | admin                 who is answerable for the row
    key      natural key for a structured kind; null for prose
    data     JSON, validated against the kind's schema at write time
    UNIQUE (channel_id, kind, key) WHERE status='active' AND deleted_at IS NULL
                                     AND kind != 'finding'

`channel_id = '*'` is "true everywhere", replacing `base.yaml`. `text` stays
prose, guarded and bounded; a structured kind's payload goes in `data`, so
the guard and the character limit never run over something that is not a
sentence. A model may not update, supersede or delete an `origin=admin` row.

`finding` is outside the index (ticket 09's review). As first written the
index held a room to one active finding per `service:error_code`, which
contradicts this spec's own "the few matching findings, newest first" and
"several findings that say the same thing are the signal to write a
runbook": the second diagnosis of a known fault could not record what it
found. The key stays on a finding because Diagnose matches on it.

**No kind reaches the system prompt.** `instructions` stays fixed so the
provider's cache reuses it. Seven kinds reach the per-call *input*; five
never reach a model at all.

| # | Kind | Written by | In a prompt | Read by | How many |
|---|---|---|---|---|---|
| 1 | `fact` | model + admin | input | extractor, Diagnose | all active |
| 2 | `constraint` | model + admin | input | extractor, Diagnose | all |
| 3 | `decision` | model + admin | input | extractor, Diagnose | all |
| 4 | `finding` | model (Diagnose) | input | Diagnose, extractor | the few matching `service:error_code`, newest first |
| 5 | `voice` | model (responder) + admin | tool result (`memory_search`) | responder | by the responder's query |
| 6 | `runbook` | admin only | input | Diagnose | only those whose `when` matches the case |
| 7 | `summary` | model only (summariser) | input | triage, responder | one per channel; replaces `derived` |
| 8 | `project` | admin only | never | code: `Resolve`, `SearchCode` | |
| 9 | `service` | admin only | never | code: `Resolve`, `FetchLog`, `CheckDeploy` | |
| 10 | `route` | admin only | never | code: `Resolve` | |
| 11 | `dependency` | admin only | never | code: `TraceDependency`, `InspectDatabase` | |
| 12 | `person` | admin only | never raw | code; names are already rendered into the transcript | |

Schemas:

    fact, constraint, voice   text
    decision                  text, data{decided_on?}
    finding                   text, key="<service>:<error_code>",
                              data{task_id, service, error_code?, refs[], confidence}
    runbook                   text = the steps, in words; key = short name;
                              data{when: {services[], error_codes[], path_patterns[], keywords[]}}
    summary                   data = RoomSummary's four fields
    project                   key=name; data{name, repo_path, default_branch, stack, docs_paths[], error_codes_doc?}
    service                   key=name; data{name, project,
                                prod{cluster, namespace, app},
                                dev{kube_context, namespace, pod_pattern}}
    route                     key=domain; data{domain, env: dev|production, service}
    dependency                key="<from>-><to>"; data{from_service, to_service, via: http|queue|webhook,
                                join_key, db_checks[{table, key_column, state_column}]}
    person                    key=discord_id; data{discord_id, name, role, team}

`service` is separate from `route` because a dev and a production domain
point at one service; merged, the pod pattern is typed twice and drifts. The
lookup is `route → service → project`, and `dependency` joins service to
service.

**A runbook is how the operator reasons about one kind of fault, in words**
— "ERR3xx on ReelMe's order API is Midas speaking; read Midas's log first,
joined on userId; if Midas is clean, look at `transactions.state`". It is not
a fact (code looks those up), not a finding (one occurrence), not a skill
(not tied to a product). `when` is structured so *code* picks which runbook
applies; `text` is prose so the *model* follows it. Several findings that
say the same thing are the signal to write one.

**The model sees and writes five kinds only** — `fact`, `constraint`,
`decision`, `finding`, `voice` — the enum the memory tools already expose.
`MemoryKind`'s own docstring gives the reason: a value that cannot be told
apart from its neighbours is one a model places at random.

**What a kind changes about a prompt**, in order of effect: which agent
receives it at all (triage still receives nothing but `summary`); whether it
reaches a model or only parameterises a tool call (the five structured kinds
are why a model never chooses a namespace); how many rows are selected; the
label it is rendered under — `runbook (admin)`, `finding (model, task 42)` —
so provenance is visible, as `room_facts` already does for layers; and its
position: admin kinds first, `finding` after, the case's evidence last, so
two cases on one service share a prefix.

**`reader_for(kind)` becomes `readers_for(kind) -> frozenset`**, because
`finding` has two readers and a structured kind's reader is `code`. The test
pinning it is rewritten in the same change.

This supersedes D3 and D9's "in the channel context" and ticket 07's "the
operator writes a file": the knowledge is rows of kinds 6 and 8–12, entered
in the UI.

## Architecture v3 (agreed 2026-09-17) and its review

Supersedes the node lists above where they differ. Agreed by the operator:
checks gather in parallel; every node goes through one invoke; one result
envelope.

    Prepare → Resolve → Notify → Gather → Diagnose → Explain → Report
                                              └────(asking / inconclusive)────┘

**One invoke.** The signature is the engine's own — `(state, deps) → result`.
What is added is `DAGRunner._invoke(node, state, deps)`: the node's timeout,
its retry (explicit exception list, doubling backoff), any other exception
turned into a result, and one `node_runs` row per attempt. No node wraps
itself. Plus `dag_version` in the checkpoint key.

**One envelope.** A node returns an `Action` (ends the run) or a JSON dict
with `status: ok | empty | skipped | timed_out | error` and `reason`. One
predicate, `timed_out → Report`, serves every node; the board renders any
node without knowing what it is.

**`Check`, the same contract one level down.**

    class Check:
        kind: str
        needs: frozenset[str]            # kinds that must have *finished* — not "found"
        budget_seconds: int; max_bytes: int
        def applies(self, state, gathered) -> bool | str
        async def gather(self, state, deps, gathered) -> Evidence

    Evidence = envelope + {claims[{claim, ref}], query, window, source,
                           truncated, spill_path}

`Gather` orders checks into waves from `needs` (topological), runs each wave
under `asyncio.gather` behind a semaphore, and returns `{kind: Evidence}`.
Today's waves: {CheckDeploy, FetchLog, FetchMetrics, CheckConfig} then
{SearchCode, TraceDependency, InspectDatabase}. A crash re-runs the whole
node; accepted, since every check is an idempotent read. `InspectDatabase`
builds its SELECT in code from `dependency.db_checks` — the model writes no
SQL.

**What the reasoning node is given.** Injected into the input, in this
order: `fact`/`constraint`/`decision` rows, runbooks whose `when` matches,
matching `finding` rows labelled `(model, task N, verified|unverified)`, then
every `Evidence` under the trust boundary. Tools, all reads and all
route-scoped by closure: `search_logs(search, since)`,
`read_source(path, line, radius)`, `codegraph_explore(query)`,
`read_repo_doc(path)`, `memory_search`, `memory_add` (kind `finding` only),
`fetch_skill`/`search_skills`/`read_skill_file`. Skills the operator loads:
how to diagnose (facts vs hypotheses, cite a ref, say what was not checked),
the report's shape, and per-stack reading guides (NestJS `dist/*.js` frames,
the JSON log format). Answer: `Diagnosis{cause, refs[], confidence (five
rungs), conclusive, next_checks[], not_checked[{kind, reason}]}`; the answer
tool refuses a `ref` no `Evidence` holds. `Explain` is the responder with the
`Diagnosis` as input and no new tools.

**Loops.** Three, each bounded, and no fourth:
1. *Inside `Diagnose`*: a tool loop of `max_turns` (config; start at 6),
   tools withheld on the last turn so it must answer, plus the harness's one
   correction.
2. *Around the graph*: `Report` asks the reporter once; their answer changes
   the parameters, the fingerprint moves, the state is discarded, the line
   re-runs from `Resolve`. A second miss is a hand-over.
3. *Around the operator*: marking a report right or wrong labels an eval row
   and verifies that task's `finding`.
There is **no hypothesis-critique loop**; the research's measured systems
support one synthesis after a planned gather, and the eval is what would
justify adding one.

### Review findings, 2026-09-17 — read against the code

| # | Severity | Finding | Fix |
|---|---|---|---|
| A | critical | Approval is per **task**: `Task.approved_at` is written once (`db.py:2221`) and the sendable-rows predicate reads it (`db.py:1657`). Approving "đang xử lý" approves the brief queued later — it goes out unread. | `approved_at` moves to the `Outbound` row; the predicate reads the row. |
| B | high | The pool is sequential (`run_once`: `for task … await self._act(task)`). A five-minute graph blocks every other task for five minutes. | Bounded concurrency in the pool; long graphs take a slot. |
| C | high | The reporter's curl carries their Bearer token. `scrub` covers logs and the board, not the prompt sent to the provider, the report file, or a spill file. | `Prepare` replaces auth header *values* before anything else; names kept. |
| D | high | Logs, source and rows are reporter-influenced text; `Diagnose` reads them and can write memory directly — a persistent injection path. | Evidence goes in quoted; a `finding` is `unverified` until the operator marks that report right, rendered with that label. |
| E | medium | Two clocks. A node timeout cancelling a harness run is a `BaseException` the retry loop never sees (`CLAUDE.md` records this once already). | For model nodes `_invoke`'s timeout = harness `timeout_seconds` + margin, checked at config load. |
| F | medium | The line re-runs on new parameters, so `Diagnose` writes the same finding twice. | `finding` key includes `task_id`; the write supersedes. |
| G | medium | `env = unknown` searching Loki broadly by path matches other products (`/v1/auth/login` exists everywhere). | Search only the channel's own `service` rows; none → `skipped`, with the reason. |
| H | medium | `git worktree add` writes into the operator's `.git`; image tag → git ref is assumed, not known. | Friday's own mirror clone under `data/repos/`; mapping on `project`; unknown → default branch and a `not_checked` line. |
| I | medium | Asking through a `Reply` is a workaround for `auto_ask` off; an unapproved notice can outlive its usefulness. | `_ask` queues *with approval* when `auto_ask` is off (every graph benefits); `Report` withdraws a stale notice. |
| J | low | `Explain` runs when there is nothing to explain. | One edge: `Diagnose → Report` when asking or inconclusive. |
| K | low | Tools on `Diagnose` reopen the loop the research argues against. | Keep `max_turns` small; the eval runs tools-on vs tools-off. |
| L | — | Nothing above is measurable without the eval. | Ticket 14. |

### Ticket mapping under v3

01 stands (params, env rule; routing reads rows). 02 and 03 become the two
`LogSource`s behind the `FetchLog` check. 04 is `CheckDeploy` + `SearchCode`
with finding H's clone. 05 is `Diagnose` as specified here. 06 is the line,
`Notify`, `Explain`, `Report`. New: 11 engine, 12 approval per row, 13 pool
concurrency, 14 eval.

### If Friday ever edits code

Not agreed and not planned — the operator's rule today is reads only. Written
down so the present design does not block it:

    … Report ─(operator: "fix it")→ Workspace → Fix ⇄ Verify (≤3, inside one node) → ReviewDiff → [approval, per row] → OpenPR

A separate task type fed by a frozen `Diagnosis` + `Evidence`; a separate
agent (`Fix` is not `Diagnose` — Aider's architect→editor pairing measured
85.0% against 55.6–79.7% single-model); a separate layer-1 capability,
`CodeWorkspace`, confined to Friday's own clone on a branch
`friday/task-<id>`, with its own allow-list; `Verify` is checks in code
(tests, lint, a diff guard: path allow-list, max lines, no CI, migrations or
secrets files); the second gate returns — `Harness.checkpoint`/`resume` are
still there and tested; the only outward act is a pull request, never a push
to a shared branch and never a deploy. Findings A, C and H are prerequisites,
not extras.

## Architecture v3.1: gather first, investigate only on failure (2026-09-17)

The operator asked two things of v3: what `Diagnose` can fetch for itself,
and why this is a graph at all rather than a planner and an executor with
every tool, under a scoring supervisor. The answer they chose is a tier, not
a replacement.

    … Gather → Diagnose ──conclusive──────────────▶ Explain → Report
                   ├──needs the reporter's response──────────▶ Report
                   └──inconclusive──▶ Investigate ──concluded─▶ Explain
                                                   └─still not─▶ Report (HandOver, with what was tried)

**Why `Gather` exists when `Diagnose` has tools.** The rule that divides
them: *known in advance to be needed → `Gather`; only known after reading →
a tool.* The operator's own routine names the first set — environment, pod,
log, stack, version — and no case skips them. Handing those to a model costs
a model turn each (sequential, the whole context resent), loses certainty (a
model skips steps; HolmesGPT's prompt has to shout "ALWAYS check application
logs"), and makes ticket 14's frozen evidence impossible, since evidence a
model gathered is different every run. ReWOO's measurement is 5× the tokens.

**A `Check` has two faces, and is written once.** `gather()` serves
`Gather`; a tool face serves `Diagnose` and `Investigate`. Both return an
`Evidence` that joins the run's evidence set, so the ref check and the
report do not distinguish them. **Every "where" parameter of a tool face is
a closed enum built by code from knowledge rows** — the model supplies a
value, never a place:

    search_logs(service, search, since)      service ∈ {resolved} ∪ {to_service of its dependencies}
    db_lookup(check, key_value)              check ∈ names of declared db_checks; no SQL
    query_metrics(service, metric, window)   metric ∈ {error_rate, latency_p95, restarts}
    read_config(service)
    read_source / codegraph_explore / read_repo_doc   Friday's clone, the running ref
    memory_search; memory_add(kind=finding)
    fetch_skill / search_skills / read_skill_file

What falls outside an enum goes to `next_checks`, and is the signal that a
knowledge row or a `Check` is missing.

**`Investigate`** is the planner-and-executor the operator proposed, scoped
to one node and one context (MAST attributes ~32% of multi-agent failures to
agents misaligned with each other; Cognition's advice is a single thread). It
runs only when `Diagnose` is inconclusive for a reason other than a missing
reporter response — ADaPT's "decompose only on failure", measured 27–33
points over both ReAct and plan-and-execute. Same tool faces, same closed
enums, a turn ceiling and a clock from config, and its answer is a
`Diagnosis` through the same ref check. **This reverses, inside one node,
DESIGN's "a model never chooses the next step"**; the graph's shape is still
code. The "scoring supervisor" is ticket 14: the eval runs three variants —
tools off, tools on, `Investigate` on — against the operator's labelled
cases, and the numbers decide whether the node stays. A model grading a
model is what D18 already rules out.

Loops are now four, each bounded: inside `Diagnose`, inside `Investigate`,
around the graph (the reporter's one answer), around the operator (marks).
Still no critique loop.

## Architecture v3.2: one Source, two users; a Collector behind one tool (agreed 2026-09-17)

Supersedes v3.1. **`Investigate` is withdrawn**: it was a second reasoner
doing `Diagnose`'s job with a larger turn budget, and the operator could not
tell the two apart because there was nothing to tell. What replaces it is the
operator's own proposal, and it is a real division of labour: **`Diagnose`
only reasons; when it needs something more it calls one tool, `collect`, and
a sub-agent that only fetches answers it.**

    Prepare → Resolve → Notify → Gather → Diagnose → Explain → Report
                                   │          │ ▲
                         formulas  │  collect │ │ Evidence
                                   ▼          ▼ │
                                 Sources ◀── Collector (sub-agent; wrapped primitives)

**Three layers under one confusing name.** "Check has two faces" (v3.1) was
wrong: `ReadFailingCode.gather(state)` takes no query — it derives one from
the stack frame — while a tool takes a query. The signatures differ. What is
shared is the layer beneath:

- **Source** (layer 1) — read-only primitives, written once, knowing nothing
  of agents.
- **Check** (in `Gather`) — a *fixed formula* over those primitives; its query
  is derived from data by rule; no model; runs on every case; does only what
  needs **no judgment**.
- **Collector tool** — the same primitive behind a thin wrapper, handed to a
  model; its query is thought up by the model from `Diagnose`'s question;
  loops until it has enough; runs only when `Diagnose` asks.

The difference is **who decides what to look for**, never what is called.

| Source | Primitives | Check in `Gather` (formula) | Collector tool (wrapped primitive) |
|---|---|---|---|
| `LogSource` {Loki, SshKubectl} | `query(service, search, start, end, limit)` — the kubectl one runs `ssh <host> kubectl -n <ns> logs <pod> --since … \| grep …`, composed in code, grep on the host | `FindRequestLog`: correlationId from the response → path + identifier, 6 h | `search_logs(service, search, since)` |
| — same source — | | `TraceDependency`: the same request in `to_service`, by the knowledge's join key | — same tool, `service` enum includes dependencies — |
| `CodeSource` {Friday's mirror clone @ref} | `read`, `grep`, `explore`, `doc` | `ReadFailingCode`: stack frame → `src/…:line` ± N, callers/callees one hop | `read_source`, `search_code`, `codegraph_explore`, `read_repo_doc` |
| `DbSource` {McpSelect} | `lookup(check_name, key_value)` — no SQL at any layer | `InspectDatabase`: every `db_check` declared for the dependency, keyed from the log | `db_lookup(check, key_value)` |
| `MetricSource` {Prometheus} | `series(service, metric, start, end)` | `MetricsAroundRequest`: error rate and p95, ±30 min | `query_metrics(service, metric, window)` |
| `DeploySource` {k8s, release read tools} | `running(service)`, `history(service, since)`, `events(service, since)` | `CheckDeploy`: running version, last rollout, events in the window | `deploy_history(service, since)` |
| `ConfigSource` {k8s configmap} | `configmap(service)`, `env(service)` | `CheckConfig`: the service's config, dev against production where both exist | `read_config(service)` |

Checks renamed after the fixed thing each does, because the shared names
caused the question: `FetchLog → FindRequestLog`, `SearchCode →
ReadFailingCode`, `FetchMetrics → MetricsAroundRequest`.

**The wrapper does four things the primitive does not**, which is why a model
never holds a primitive: the *where* is fixed by closure from `Resolve`
(service is a closed enum from knowledge rows; repo and ref are not
parameters at all); ceilings on radius, hits and bytes, with truncation
said out loud; de-duplication by ref against what is already gathered; and
the result becomes an `Evidence` in the run's set before the model sees it.

**The boundary, in one sentence:** `Gather` does what needs no judgment.
So a fallback that needs some — no stack, grep the error code, forty hits —
does not stay in `Gather`: the check returns `empty` with "too many hits,
needs a narrower question", and `Diagnose` decides whether to `collect`.

**`collect`, and the Collector.**
- `Diagnose`'s tools are now: `collect(request)`, `memory_search`,
  `memory_add` (kind `finding`), the three skill tools, and its answer tool.
  It holds no fetching tool. One tool instead of seven, for a model measured
  answering outside a closed enum.
- `request = {question, service (closed enum), window?, identifiers{}}` —
  structured, because MAST attributes ~32% of multi-agent failures to agents
  misreading each other and Cognition's whole argument is lost context.
  `Diagnose` says *what* it needs, never *where*.
- The Collector `answers=Evidence`. Its shape has **no `cause`**; it may not
  conclude. Every claim carries a **verbatim quote**, and code checks the
  quote occurs in the output of a tool it actually called — `CLAUDE.md`'s
  "verbatim material is stored whole and pointed at, never paraphrased", and
  DiagGuard's "misinterpreted findings", in one check.
- It is shown the **catalogue** of evidence already gathered — kind, query,
  ref, not content — so it does not fetch it again.
- **Not `agent.as_tool()`.** That runs the sub-agent outside `_settle`: no
  clock, no retry list, no budget, no record. `collect` is a tool in
  `friday/tools/` whose body calls `collector_harness.run_structured(...)`,
  so every model call still passes the one seam and each collect has its own
  `model_calls` rows. `Diagnose`'s timeout must cover its collects (finding E).
- Two ceilings, from config: collects per run (start at 3), turns per collect
  (start at 5). Skills about LogQL, kubectl and CodeGraph are the
  Collector's; they stay out of the reasoning prompt.

**A supervisor in code scores every `Diagnosis`** — grounding is a hard gate
(every ref exists, `cause` cites one); then specificity (a ref to a log line,
a `file:line` or a row, not a service name — OpenRCA 2.0: right service 76%,
grounded path 61.5%), rung consistency (`validated` needs `found` log or
database evidence; an empty `FindRequestLog` caps at `speculation`), coverage
of applicable checks, and runbook adherence. Routing: `conclusive` **and**
the score clears the threshold → `Explain`; needs the reporter → `Report`
asks; otherwise `Report` hands over with `not_checked`. The weights and the
threshold are the eval's to set. An LLM judge is an eval variant, used online
only once its scores agree with the operator's marks.

**Loops, four, each bounded:** inside `Diagnose` (collects), inside the
Collector (tool turns), around the graph (the reporter's one answer), around
the operator (marks → verified findings and eval labels). No critique loop.

**Agents that call a model:** the extractor (node 0), `Diagnose`, the
Collector (zero to N runs), `Explain`. An easy case is three calls; setting
the collect ceiling to 0 is the eval's baseline.

## Architecture v3.3: Gather stops fetching (operator, 2026-09-22)

Supersedes v3.2's division of labour. The operator's call, and the argument
is the one this board has been making about the database all week, applied
to everything else:

> "Gather context sẽ làm việc như là 1 node gom lại toàn bộ các metadata để
> cho Diagnosed làm việc mà nó giỏi nhất … Còn phía Diagnosed agent sẽ dựa
> vào các thông tin đó để đọc và sử dụng các tool để truy vấn."

    Prepare → Resolve → Acknowledge → Diagnose(tools) → Report
                 │                        │
        metadata │                        │ read_log · read_code
                 ▼                        ▼ what_code_means · query
              (no reads)                Sources

**`Gather` gathers metadata, not data.** Where the repository is, which file
a frame names, what the service is called, which cluster and namespace it
runs in, which environment, which release tag is running, which `db_id`s
this room may read, which services it talks to. It opens nothing.

**The Check layer is withdrawn.** v3.2 called it "a fixed formula over
primitives, its query derived from data by rule, doing only what needs no
judgment". The week measured what that judgment actually costs:

| the formula decided | what it got wrong | found |
| --- | --- | --- |
| which needle to search on | the endpoint path matched every *other* caller — 18 dossier lines where the id gives 8 | 2026-09-21 |
| how wide a window | 400 lines covered 84 seconds of the 35 minutes asked for, and the request was outside it | 2026-09-21 |
| when to widen | one `WARN` a minute disarmed the rule permanently | 2026-09-21 |
| which table answers a question | it cannot be enumerated in advance at all | 2026-09-21, the reversal |

Every one of those is a judgement wearing a rule's clothes. A model that can
search, look, and search again handles them the way a person does.

### The boundary, and it is a measured one

**A tool is not the back end.** Measured on the captured production case:

| | |
| --- | --- |
| one raw `loki_query_range` window | 171 KB ≈ **43,654 tokens** |
| the same read, narrowed at the source | 1.5 KB ≈ 388 tokens |
| what `distil` cut it to | **8 lines** |

So `Diagnose` is handed `read_log(needle, minutes_back)`, never
`loki_query_range`. Inside the tool: the narrowed read, `distil`, the
histogram, the numbering. **The model decides what to look for; code decides
what comes back.** That is v3.2's own sentence, and it is the whole of what
v3.3 keeps from it.

**This is also why the Collector sub-agent goes.** v3.2 put a sub-agent
between `Diagnose` and the sources so raw volume never reached the reasoner.
Once the tool itself distils, the volume is already gone and the sub-agent is
a turn, a prompt and a failure mode buying nothing.

### What has to survive, each for a measured reason

1. **The grounding gate.** `refs` are ids off lines *code* numbered — asked
   to quote, the configured model succeeded 32/40; asked to point, 20/20.
   With tools the index **accumulates across calls**: every line any tool
   returns is given an id, and `refs` are checked against that. Without this
   the gate quietly stops meaning anything.
2. **`distil` stays a pure function over lines.** It is what lets a test
   assert "the decisive line survived" with no cluster in the room.
3. **A ceiling on tool calls.** `api_issue.timeout_seconds` is 420s against a
   node sum of 400; with N calls the clock and the token budget become the
   real bound rather than a formality.
4. **Out of reach is not not-found.** The retention answer is a property of
   the read, so it moves into the tool's answer, not out of existence.

### What it costs, said before it is built

- **One model call becomes several.** The captured case diagnoses today in
  8.5 s and one call.
- **It stops being deterministic.** The same case may investigate two ways.
  Which makes **ticket 14 a precondition rather than a nicety**: without a
  set of labelled cases, nothing can say whether this architecture diagnoses
  better or worse than the one it replaces. This spec said 14 could wait. It
  cannot any more.

### Order, and why the last step is separate

1. This section, and ticket 15 rewritten to match — read before any code.
2. Wrap the existing sources as tools. The graph does not change; nothing
   behaves differently yet.
3. `Diagnose` uses the tools. `FindRequestLog` and `ReadFailingCode` stay,
   as the fallback.
4. Measure: same conclusion on the captured case, and what it cost.
5. **Remove the two nodes only if step 4 says it is not worse.** There is one
   labelled case today, and retiring two nodes that work on the strength of
   n=1 is the mistake this board keeps writing down.

## Diagnose's context: a dossier, not the data (agreed 2026-09-18)

Read against Anthropic's "Effective context engineering for AI agents"
(fetched 2026-09-18): attention is a budget that shrinks as context grows;
keep lightweight identifiers and load data just in time; hybrid — retrieve
some up front for speed, explore further at discretion; a sub-agent returns a
distilled 1,000–2,000 tokens; start minimal and add rules from observed
failures.

**`Diagnose` reads a dossier. Raw data stays on disk behind a ref.** Every
`Evidence` splits in two: `claims[{claim, quote, ref}]` — at most N lines,
each ≤ 300 chars — which enters the context, and `spill_path`, the whole raw
payload, which does not. Wanting the raw is `collect` or
`read_evidence(ref, radius)`. **The distillation is by rule, in code**,
wherever the data's structure allows:

| Check | Rule, no model | In context |
|---|---|---|
| `FindRequestLog` | the request's own lines (measured 2026-09-18: exactly 2 — INFO + ERROR); then an **error-code histogram** of ±5 min (counts, not lines — the raw window is a median 58 lines, up to 885); then same-user and same-path errors, capped at 8 and reported as "8 of N" | ≤ 12 lines |
| stack | frames under `/app/dist/src/` kept, `node_modules` dropped, ≤ 5; `errorCode`/`errorMessage` split out | ≤ 8 lines |
| `ReadFailingCode` | ±15 lines around the first frame, the enclosing function's name, one hop of callers | ≤ 40 lines |
| `CheckDeploy` | version, last rollout time, count of error events | 3 lines |
| `MetricsAroundRequest` | error rate and p95 before/during/after — numbers, no series | 3 lines |
| `CheckConfig` | only the dev/prod diff, or only keys the code at the frame reads | ≤ 10 lines |
| `InspectDatabase` | the row by key, only the declared `db_checks` columns | ≤ 5 lines |

**The dossier has a budget and a priority.** Code assembles it under a token
estimate (chars ÷ 4, as `extraction_budget_tokens` does), filling in the
order that decides a case: the request's own lines → stack → code at the
frame → deploy → dependency → database → metrics → config. Past the budget a
check contributes one line — "present, ref X, not included" — and
`not_checked` records "cut for budget", so `Diagnose` can `collect` it.

Tokens per run — the log part **measured 2026-09-18** (ticket 16), the rest
still estimates: instructions ~1,500 (fixed, cached); knowledge ≤ 1,000;
notes from a previous run ≤ 500; dossier — logs **~170 measured, 360 max**,
code ~500 per frame (estimate), the rest under 200 — so a 5,000 budget is
~4× an easy case and the slack goes to code context; each `collect` ≤ 1,500,
at most 3. Easy case ~4,000; hard ~9,000. The refuted alternative, every
ERROR line of ±5 min, measured at ~12,000 for the logs alone.

**Order, for speed and for attention.** Fixed parts first (instructions,
then knowledge, then notes), the dossier last and adjacent to the question —
two cases on one service share a prefix, and the decisive lines sit where a
long context is read best, not in its middle. An easy case ends in one turn.

**Self-questioning, without another model call.** Three tiers, cheapest
first:
1. *The answer shape forces it.* `Diagnosis` gains `hypotheses[{claim,
   status: supported|refuted|open, refs[]}]`, `alternatives_rejected[
   {hypothesis, why, ref}]` (at least one when `conclusive`), and
   `distinguishing_check`. Filling `alternatives_rejected` requires having
   thought of one.
2. *The code supervisor answers as a tool, with specific objections* — "ref
   `loki:…` does not exist", "`validated` with no `found` log evidence",
   "runbook step 2 has no evidence", "cause names Midas, no ref is a Midas
   line" — and the model corrects in the same run, one turn. An
   evaluator-optimizer whose evaluator is code.
3. *One sentence in the prompt*: name the strongest alternative explanation
   and the evidence that would rule it out; if one `collect` settles it, do
   it. Bounded by the collect ceiling.
No fourth tier: an LLM critic is an eval variant until its scores agree with
the operator's marks.

**Structured notes across the outer loop.** When the reporter answers and
the line re-runs, the previous `Diagnosis` in the state is distilled by code
into ≤ 500 tokens — hypotheses and their status, what was collected — and
injected, so the second run does not reason from zero. `finding` does the
same across cases.

**Build order, per the article's last advice:** minimal prompt, the dossier
with its budget, the shape with `alternatives_rejected`, the grounding gate;
then let ticket 14's failures add rules. No pre-written list of edge cases.

## Before the board: measure, then a slice (agreed 2026-09-18)

The design was scored by its author: 8 for shape and boundaries, 6 for
knowledge, 5 for the Collector, 4 for the distillation rules, 3 for every
number in it, and nothing for having run. The operator asked for no part
below 7. Two tickets come before everything else:

- **16 — measure before building.** Dossier size on 20 real failed
  requests, reporter delay on past tasks, Source latencies, error-code
  frequency, seed rows drafted from Loki labels — and a half-day probe of
  the configured model on the three things this design asks of it (pick an
  existing ref, quote verbatim, finish a tool loop). Below 90% on the first
  two, `Diagnose` moves to a stronger model or the Collector ships switched
  off; not a lowered bar.
- **00 — a vertical slice.** `Prepare → Resolve (two typed rows) →
  FindRequestLog → ReadFailingCode → Diagnose (no tools) → Report
  (HandOver)`, through the real pool, outbox and board, on five remembered
  cases. Blocked only by 12. It may be thrown away; what it answers is not.

The distillation rule becomes **recall-first** — a request's own lines and
every ERROR/WARN of its correlationId are never cut; only the surroundings
are — with a **coverage test**: for each labelled case the operator names
the decisive line, and a pure-code test asserts the rule keeps it. One
automatic widening of the window when a dossier has neither an ERROR line
nor a stack, recorded in `not_checked`.

Every number in this spec is an estimate until it carries a "measured on"
tag; `config.yaml` gets no number without one.

**Dev is behind SSH (measured 2026-09-18).** There is no kubeconfig for dev
on the operator's machine; `kubectl` runs on the dev host, reached by
`ssh dev`. `KubectlSource` is therefore `SshKubectlSource`: the command is
composed in code from `service.dev.{ssh_host, namespace, pod_pattern}` and
the window, filtered on the host, and the model never sees or writes any
part of it. SSH is a full shell, so the composition is the guard; a
`command=` restriction in the host's `authorized_keys` is the second lock,
worth asking for. Dev log history is bounded by the pod's last restart.
D14 stands: the SSH key is what the machine has.

**Pointers, not quotes (measured 2026-09-18, ticket 16).** Asked to quote a
JSON log line verbatim, MiniMax-M3 succeeded 32/40 and every failure put the
line's own keys into the answer's arguments; asked to point at the line by
its correlationId, it succeeded 20/20. So every model-written `Evidence`
carries pointers only, and code fills `quote` from the tool output it holds.
A pointer that does not resolve is refused like any wrong ref. This replaces
"code checks the quote occurs in a tool output" in v3.2.
