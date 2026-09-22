# 05: Diagnose, say what was not checked, and escalate on three conditions

**What to build:** The diagnosis agent and node, its answer shape, its
memory tools, and the guarded step to the database.

**Blocked by:** for the `[db]` step, the operator's decision about its
shape. Nothing else (2026-09-22). (Was also 07: knowledge rows enrich a
diagnosis, they do not gate building one — `Diagnose` with an empty
knowledge table must work, and is the baseline.)

**Decisions:** D8, D9, D12.

**Status:** part done (2026-09-22). `alternatives_rejected` is in the answer
shape and enforced: `conclusive: true` with nothing ruled out is refused, and
so is an entry with an empty hypothesis, an empty reason, or a `ref` naming a
line the model was not shown. That is the spec's first tier of
self-questioning — the shape forcing the question — and the only one that
costs no second model call.

**What is left:** memory tools for the diagnosis agent, which is handed a
model and nothing else; and the `[db]` step, which stays deliberately unwired
— `friday/sources/db.py` has no caller until the operator decides its shape.

## What

**Revised 2026-09-18 (spec, "Diagnose's context"):**
- `Evidence` splits into `claims[{claim, quote, ref}]` (in context) and
  `spill_path` (on disk); each check distils by rule, under the per-check line
  caps in the spec's table. `read_evidence(ref, radius)` is the just-in-time
  read.
- A dossier builder with a token budget (`diagnose.dossier_budget_tokens`)
  and the spec's priority order; a cut check leaves one line and a
  `not_checked` entry "cut for budget".
- Input order: instructions · knowledge · notes from the previous run ·
  dossier · question. A prefix-stability test, as triage has.
- `Diagnosis` gains `hypotheses[]`, `alternatives_rejected[]` (≥ 1 when
  `conclusive`), `distinguishing_check`.
- The code supervisor's objections return as the answer tool's output, one
  correction turn.
- Previous-run notes: the stored `Diagnosis` distilled to ≤ 500 tokens and
  injected on a re-run.


- `Harness(answers=Diagnosis)`: `cause`, `evidence` (verbatim lines),
  `code_path`, `suggested_fix`, `confidence`, `conclusive`, `not_checked`.
- Memory tools wired, writing `finding` scoped to the channel; the
  instruction-shape guard applies unchanged. The channel's dependency
  knowledge (ticket 07) is injected the way domain kinds already reach the
  extractor.
- Escalation predicate, in code: `not conclusive and no stack for this
  request and the dependency knowledge names a table and key`. Only then a
  read through `db-generic`. First fact to fetch: its tool list, and whether
  the account is read-only on both environments — the operator did not
  answer that, and the ticket must.
- A skill slot: the agent is told which skill to fetch for the report's
  shape, from `config.yaml`.

## Verify

- Tests for each of the eight combinations of the three conditions; only one
  escalates.
- The `Reply`-construction anchor test stays green: this node writes no
  `Reply`.

## Owed by the slice (ticket 00, 2026-09-20)

The slice built `Diagnose` as one model call over fixed evidence, no tools,
answering `Diagnosis{cause, confidence, conclusive, refs, next_checks}` with
**pointers, not quotes** — every line of the prompt carries an id, the model
names ids, code puts the text back, and a pointer that resolves to nothing
voids the answer. So does calling an answer conclusive while pointing at
nothing.

Still owed here:

- **`alternatives_rejected`**, and with it `hypotheses` and
  `distinguishing_check`. The spec's build order is "minimal prompt, the
  dossier with its budget, the shape with `alternatives_rejected`, the
  grounding gate"; the slice shipped the first and the last.
- **The dossier budget.** The slice uses fixed line caps
  (`friday/dag/api_issue/logs.py`), not `diagnose.dossier_budget_tokens` and
  the priority order, and no check is cut with a "cut for budget" line.
- **`not_checked` as `[{kind, reason}]`.** The slice's is a list of strings,
  written by code rather than by the model — the nodes already know what they
  skipped, and that half is worth keeping.
- **Moving the grounding gate into the answer tool**, so a bad pointer is
  corrected inside the model's own turn budget instead of voiding the run
  after it (D8).
- **The numbers ticket 00 asks for in its answer 3** — dossier tokens, wall
  time per node, model calls — are in `node_runs` and `model_calls` but not
  in the report file. Whichever of the two is the operator's reading surface
  should carry them.


## `db-generic`, 2026-09-21 — the block exists, the catalogue does not

The operator asked for the same treatment `devops-generic` got. Half of it
is done and the other half cannot be done yet, which is worth separating.

**Done.** `config.yaml` carries a commented `db-generic` block —
`https://devops-dbx.aperogroup.ai/mcp`, streamable HTTP, its own Keycloak
client (`friday-db`, `DB_MCP_CLIENT_SECRET`). Its own client rather than
`devops-generic`'s on purpose: a role can then be given to one and not the
other, which is the only reason two identities are worth having.

**The catalogue, read the same day.** Four tools:

| tool | takes | reads only? |
|---|---|---|
| `list_databases` | nothing | yes |
| `describe_schema` | `db_id` | yes |
| `execute_mongo_query` | `db_id`, `collection`, `filter?`, `projection?` | **yes, by construction** — "MVP: find only (read-only)" |
| `execute_query` | `db_id`, **`sql`** | **no. Arbitrary SQL.** |

**`lookup(check_name, key_value)` does not exist.** The primitive ticket 15
describes is not what this server offers; the only relational tool is raw
SQL. That is the fact this ticket was told to fetch first, and it is the one
that changes the design rather than filling in a blank.

**Every database says `"permission": "reader"`.** Eleven of them, across two
ventures, and the two this room cares about are
`supermind-postgres-ai-backend-reelme-v2` and
`supermind-postgres-backend-reelme-payment`. That is the guard that holds
whatever else does not: a `DELETE` composed by accident, or by an injection
that reached the string, fails at the database rather than at our
intentions.

**What is already decided, so that reading the catalogue is the only open
question** (ticket 15): `DbSource` is `lookup(check_name, key_value)` and
**no SQL exists at any layer**. Friday never composes a query. The operator
writes a named check as a `dependency` row — table, key column, state column
— and code passes a key into it. If the server turns out to accept raw SQL,
that changes nothing here: a tool being able to do something is not the same
as this system being able to ask for it, and `friday.sources.Reads` is where
that difference is enforced.

**The one thing to measure first**, when it is authenticated: whether the
server offers anything that writes. `devops-generic` did — fifteen tools
that change production — and the allow list being one line long is what
makes that survivable.


## What `execute_query` does to "no SQL at any layer"

Ticket 15 says `DbSource` is `lookup(check_name, key_value)` — "no SQL
parameter exists". The server offers no such thing, so that sentence has to
be read for its intent rather than its letter, and the intent is worth
restating precisely now that the layer underneath does take SQL:

- **No model ever composes a query, and no tool a model can reach takes a
  `sql` parameter.** That part is unchanged and is the whole point.
- **Code composes it**, from a `DbCheck` the operator wrote — `table`,
  `key_column`, `state_column` are already fields on that dataclass in
  `friday/domain/models.py`. The model names a *check* and supplies a *key
  value*; nothing else about the statement is influenced from outside.
- **The key value is the one outside-influenced token in the string**, and
  `execute_query` has no parameter binding — one `sql` string and nothing
  else. So it has to be escaped where the statement is built, and the
  `reader` permission is the second lock behind that, not the first.

Three layers, and none of them is a prompt: the statement's shape comes from
a row the operator typed, the value is escaped by code, and the credential
cannot write. `friday.sources.Reads` is where the fourth lives — the tool
name itself has to be declared by the class that calls it.

**One thing that will bite whoever writes it.** This schema is Prisma's, so
the tables are `PSPLedgerTransaction`, `UsageTransaction`, `Purchase` —
PascalCase. Postgres folds an unquoted identifier to lower case, so a
composed statement must double-quote table and column names or fail with
"relation does not exist" on a table that plainly exists.

**And `describe_schema` is not free to read into a prompt.** The payment
database alone came back with fifteen tables including `pg_stat_statements`
and its forty columns. If a check ever needs the schema, it needs one
table's worth of it, not the database's.
