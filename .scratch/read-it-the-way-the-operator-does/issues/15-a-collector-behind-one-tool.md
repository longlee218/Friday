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
