# 15: A Collector behind one tool

**What to build:** The Source layer's six protocols, the wrapped-primitive
tools over them, the `collect` tool, and the Collector sub-agent that answers
it with an `Evidence`.

**Blocked by:** 09 (knowledge rows build the enums), 11 (timeouts that nest),
05 (`Diagnose` and the evidence set). The eval baseline — collects = 0 —
comes from 14 before this is switched on (D16).

**Decisions:** spec, "Architecture v3.2". Replaces this ticket's earlier
form, `Investigate`, which is withdrawn.

**Status:** ready-for-agent

## What

- **Sources** in one package, read-only, no agent imports: `LogSource`
  (Loki, Kubectl), `CodeSource` (Friday's mirror clone at a ref),
  `DbSource` (`lookup(check_name, key_value)` — no SQL parameter exists),
  `MetricSource`, `DeploySource`, `ConfigSource`. The checks of tickets
  02–04 are rewritten as formulas over these; nothing else touches Loki,
  kubectl, git or the database MCP.
- **Wrapped tools** in `friday/tools/`, built per run by a factory taking the
  resolved target, the knowledge rows and the evidence set: `search_logs`,
  `read_source`, `search_code`, `codegraph_explore`, `read_repo_doc`,
  `db_lookup`, `query_metrics`, `deploy_history`, `read_config`. Each: `where`
  by closure, `service` a closed enum, ceilings, de-dup by ref, result
  appended to the evidence set. Listed in `tests/test_tools.py`.
- **`collect(request)`** in `friday/tools/`, its body calling
  `collector_harness.run_structured`. `request = {question, service, window?,
  identifiers}`. Returns the `Evidence`'s claims and refs, not raw output.
- **The Collector**: `Harness(answers=Evidence)`, its own prompt module, the
  catalogue of existing evidence in its input, skills for LogQL / kubectl /
  CodeGraph. The answer carries **pointers only** (`correlation_id`,
  `file:line`, `db:<check>:<key>`); code resolves each against this run's
  tool outputs and fills `quote` itself. An unresolvable pointer is refused,
  naming the claim. (Ticket 16: quoting 80%, pointing 100%.)
- Config: `collector.max_collects_per_run` (3), `collector.max_turns` (5),
  and a load-time check that `Diagnose`'s timeout ≥ collects × the
  Collector's.

## Verify

- An `ast` test: nothing outside the Source package imports the Loki, kubectl,
  git or database clients; no tool takes a repo, ref, namespace or SQL
  parameter.
- A model naming a service outside the enum is refused in this process with
  the allowed values.
- A Collector answer with a pointer that resolves to nothing is refused;
  delete that check once and watch the test go red.
- `model_calls` holds a row per Collector attempt, correlated to the task.
- Eval: collects = 0 vs 3, reported side by side. If collects do not pay for
  themselves, the ceiling ships at 0 and says so.


## Already built by the slice (ticket 00, 2026-09-20)

`friday/sources/` exists, with the first of the six protocols and the guard:

- **`LogSource`** and both its back ends — `LokiSource` (devops MCP, tool
  name from `config.yaml`) and `SshKubectlSource` (`ssh <host> kubectl -n
  <ns> logs <pod> --since-time …`, composed in code). `Placement` — the
  address they read from — lives here too, so a source never imports the
  graph that calls it.
- **`CodeSource`'s `read` primitive**: a stack frame mapped into the
  operator's clone, with the root check that refuses a frame climbing out of
  it, and the window around the line. `grep`, `explore` and `doc` are ticket
  04's; they are not declared as empty protocols here, because a shape with
  one implementation and no second caller is a guess about the second one.
- **The `ast` test this ticket's Verify asks for**, in
  `tests/test_sources_are_the_only_door.py`: nothing outside
  `friday/sources/` starts a process or calls a tool on a server, and no
  source imports `friday/agent/`, `friday/dag/` or `friday/tasks/`. Both
  halves deleted once and watched go red.

Still this ticket's: `DbSource`, `MetricSource`, `DeploySource`,
`ConfigSource`; the wrapped tools in `friday/tools/`; `collect`; the
Collector and its prompt. The slice's checks are formulas over the two
sources that exist, which is the shape the rest are meant to arrive in.


## `DbSource` reversed, 2026-09-21 — the model picks the table

This ticket said `DbSource` is `lookup(check_name, key_value)` and "no SQL at
any layer", in **both** columns of the table above: the check *and* the
Collector tool were pre-declared. The database was the one source where a
model was not allowed to decide what to look for.

**The operator reversed it, and the argument is short.** Which table answers
a question is reasoning. A payment failure might be in `PSPLedgerTransaction`
or `Purchase` or `Subscription`, and which one depends on what the log said —
no table of pre-declared checks enumerates that in advance. "The difference
is who decides what to look for, never what is called" is this ticket's own
sentence, and the database was the exception to it.

**The reason for the exception was writes, and it does not hold.** Every
database this server offers answers `"permission": "reader"` — measured, not
assumed. A statement that is not a read fails at the database.

**Built:** `friday/sources/db.py`, three primitives —`databases()`,
`schema(db_id, table?)`, `query(db_id, sql)` — with four rules that are code
rather than prompt, each answering something a *read* can still get wrong:

| rule | what it is for |
|---|---|
| only this room's `db_id`s, and none if none are written down | eleven databases across two ventures; a missing row is a hand-over |
| `select`/`with` only, one statement | not to stop a write — the credential does that — but to make a model that misunderstood its job say so loudly |
| the rows are capped after they arrive, and say so | **measured: this server does not honour `LIMIT`** — `SELECT * FROM (three rows) LIMIT 2` came back with three |
| personal columns are `[REDACTED]` by name, the name kept | `scrub` catches a credential and not an address |

**`DbCheck` keeps its place and changes its job.** `table`, `key_column`,
`state_column` are still on the dataclass, and a declared check is still
worth having — but as the shortcut for a question asked every week, run by
code for no tokens and no reasoning, not as the only way to ask.

**Two things this does not fix, written down rather than solved.**

- A wrong query answers confidently. `UsageTransaction` is the best-named
  table in the payment schema and carries no `userId`; a model that picks it
  gets nothing and concludes there was no transaction. The grounding gate
  does not help — the empty result *is* evidence it was shown.
- The cap protects the prompt and **not the database**. A heavy read is
  still heavy on the far side, and nothing on this side can make it lighter
  while `LIMIT` is ignored. Worth raising with whoever runs the MCP.

**Still this ticket's:** the wrapped tool a model actually holds, the
`collect` tool, and the Collector. `DbSource` has no caller yet — it is the
capability, and `InspectDatabase` is ticket 05.
