# 28 (retired): a workflow that survives a restart

This ticket is retired. The original framing — "ordered set of steps, each
recording before the next begins" — is **subsumed by ticket 32** (DAG
framework) and **ticket 33** (the first DAG, `api_issue`). The DAG's
checkpoint-after-every-node satisfies every acceptance criterion below:

| Original criterion | Where it lives now |
|---|---|
| Workflow is an ordered set of named steps | DAG nodes in `friday/dag/` |
| Each step's result recorded before the next begins | `DAGRunner` saves state after every node |
| Restart continues at the first unfinished step | `load_dag_state(task.id)` + resume from the next node |
| Step + effect commit together (own store) | DAG runner writes state in the same DB transaction as the result |
| Step does not send twice on resume (external) | Ticket 33's `fix_bug` carries an idempotency key |
| State is typed | `DAGState` is a frozen dataclass; missing keys raise on read |
| Adding a workflow does not touch existing ones | `EDGE_ROUTER` is `dict[str, DAG]`; new entry, no edits |

The text below is kept for the historical record and for any reviewer who
wants to see why the ticket was retired rather than re-issued.

---

## Original ticket body

A workflow runs as an ordered set of steps, each taking the state so far, doing
one thing, and handing back what it learned. Killing the process partway through
and starting again continues from the first unfinished step instead of paying
for the finished ones twice.

This reopens ticket 07, which was closed as superseded and should not have been.
The reason given there — that a planner writes nothing until it returns, so there
is no position to resume from — is true only while a planner is one cheap
call. A step that spends five tool calls and ten minutes against a log store is
a position, and losing it costs real money.

Building rather than adopting: a graph library was measured at twenty-two extra
packages, a second HTTP client in the same container, and two of its own tables in
the one SQLite file — for a feature that is about a hundred lines here.
**Revisit that when durable resume spreads past two workflows**; below that the
library costs more than it saves, and above it the reverse.

The hard part is neither the state nor the graph. It is that **a step with a side
effect can crash after causing it and before recording that it did** — and resuming
then does it twice. For anything written to this system's own store, the step's
result and its effect commit together or not at all. For anything that has already
left the process, only an idempotency key helps, which is the same choice the outbox
already makes: better to send twice than to lose one.

Branching and running steps side by side are explicitly not the goal. Ordinary Python
already expresses both, and a graph that exists to replace `if` puts a language
between the author and their own code.

## Why this ticket is retired, not deleted

The reasoning in the original ticket — durable resume matters, build rather
than adopt, ordinary Python over a graph library — is still right. Ticket 32
embraces it: the DAG runner is ~150 lines, no graph library, no second HTTP
client, no foreign tables. The split was between "a workflow is a procedure"
(function model) and "a workflow is a graph" (DAG model); ticket 28 carried
the procedure framing because that was the only shape available in 2026-08, and
ticket 32 carries the DAG framing because that is the shape ticket 33 needs.

If a reviewer thinks the original framing is still useful — e.g. "every node is
an ordered set of named steps, recording before the next begins" — it lives on
in ticket 32's acceptance criteria, which is the new home.
