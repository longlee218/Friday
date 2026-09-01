# 32: A workflow is a DAG, not a function

**What to build:** Replace the single-planner model with a DAG framework. Each
workflow for a task type is a sequence of nodes — model calls, MCP tool calls,
deterministic code. Nodes can branch, retry, and pause for a human. The runner
holds checkpoint state so a crash mid-DAG resumes from the last completed node
rather than starting over.

**Blocked by:** 31 (DAG framework reads `original_text` from `messages`, and
depends on the seam that the extract/validate pipeline established)

**Status:** ready-for-agent

A planner that decides "trace this correlation id" by making five model calls
in sequence is not a function — it is a graph. The shape:

```
   ┌──────────┐    ┌───────────────┐    ┌────────────┐
   │ read_logs│ -> │ find_code_path│ -> │analyze_stack│
   └──────────┘    └───────────────┘    └─────┬──────┘
                                             │
                                ┌────────────┴───────────┐
                                ▼                        ▼
                          ┌──────────┐            ┌──────────────┐
                          │ fix_bug  │            │compose_reply │
                          └─────┬────┘            └──────┬───────┘
                                │                       │
                                └──────────┬────────────┘
                                           ▼
                                      outbox row
```

The edge from `analyze_stack` to `fix_bug` is conditional. `fix_bug` may raise
`PauseForHuman` when it cannot determine a fix safely. The runner catches
that, parks the task, and tells the operator via Discord.

## Why DAGs and not just multi-call planners

Ticket 21 ("reply-to what message?" discussion) and ticket 28 ("a workflow that
survives a restart") both point at the same thing: a planner that calls five
MCP tools in sequence is doing what DAG frameworks do, badly. Two consequences
of staying with the function model:

1. **No resume.** A planner that crashes after step 3 of 5 starts over. For
   "read logs from Loki, find code path, run tests, fix, commit" that is
   3-10 minutes wasted per restart. The ticket 28 discussion already named
   this as the motivating problem.

2. **Operator intervention has nowhere to land.** A planner either returns
   Ask/Reply/Park or it does not. When the planner *cannot* decide because the
   fix is unsafe, the current shape forces it to invent a guess. The DAG
   shape admits a fourth state — `PauseForHuman` — that the planner raises
   when it would otherwise fabricate.

## Pattern: each node is an Agent

The OpenAI Agents SDK already supplies the right primitives — tool calls,
multi-agent routing, retries, tracing. Each node is one `Agent` instance:

```python
from agents import Agent, Runner, function_tool

@function_tool
async def query_loki(query: str, since: str) -> str:
    """Query Loki logs. `query` is a LogQL selector, `since` is RFC3339."""
    return await loki_mcp.call("query_range", {"query": query, "since": since})

read_logs = Agent(
    name="read_logs",
    instructions="Given a correlationId, query Loki for matching log lines in the last hour.",
    tools=[query_loki],
)
```

A node is `async def node(task_state) -> Result`. The body can call
`Runner.run(read_logs, prompt)` or `Runner.run(fix_bug, prompt)` or anything
else — the DAG runner does not care what the node is, only that it has the
shape.

## The shape

- `friday/dag/__init__.py` — `DAGRunner`, `DAG`, `Node`, `Edge`, `NodeResult`.
- `friday/dag/state.py` — `DAGState`, serialised per task. Keys are node
  names, values are whatever the node returned.
- `friday/dag/pause.py` — `PauseForHuman` exception. Raised by a node when
  it cannot proceed without an operator.
- `friday/dag/router.py` — `EDGE_ROUTER: dict[str, DAG]` mapping task type
  to the DAG that runs it.

## Pipeline

`WorkflowRunner._plan(task)` becomes:

```python
async def _plan(self, task):
    dag = EDGE_ROUTER.get(task.type)
    if dag is None:
        return Park(f"no DAG for {task.type!r}")

    state = await self._db.load_dag_state(task.id) or DAGState.empty()
    runner = DAGRunner(dag, task=task, db=self._db, initial_state=state)

    try:
        result = await runner.run()
    except PauseForHuman as exc:
        return Park(reason=str(exc))

    if isinstance(result, Reply):
        return result
    return Park(reason=f"unhandled DAG outcome {type(result).__name__}")
```

## Conditional edges

Each edge declares a predicate. The runner follows the predicate's `True` branch.

```python
class Edge:
    src: str
    dst: str
    when: Callable[[DAGState], bool] = lambda _: True
```

`fix_bug` only runs if `analyze_stack` produced an actionable cause:

```python
def has_cause(state) -> bool:
    return "analyze_stack" in state and "traceback" in state["analyze_stack"]

dag = DAG(
    nodes=[read_logs, find_code_path, analyze_stack, fix_bug, compose_reply],
    edges=[
        Edge("read_logs", "find_code_path"),
        Edge("find_code_path", "analyze_stack"),
        Edge("analyze_stack", "fix_bug", when=has_cause),
        Edge("analyze_stack", "compose_reply", when=lambda s: not has_cause(s)),
        Edge("fix_bug", "compose_reply"),
    ],
)
```

## Checkpoint

After every node, the runner calls `db.save_dag_state(task.id, state)`. On
restart, `load_dag_state(task.id)` returns the last saved state; the runner
picks up at the next node, not at the start.

Edge case: idempotency. A node that calls an external API (Loki query, code
write) must be safe to retry. Ticket 34 records the contract.

## PauseForHuman → Park

A node that needs operator input raises `PauseForHuman(reason)`. The runner
catches it, parks the task with the reason, and writes a `HELP_WANTED`
outbound row. The operator sees it via Discord DM (current `HELP_WANTED`
mechanism), responds, and the runner resumes from the paused node on the
next pass.

The pause reason must be structured — not just "needs human" — so the
operator can act without reading the runner log:

```python
@dataclass
class PauseForHuman(Exception):
    question: str              # what the operator is being asked
    options: list[str] = field(default_factory=list)  # possible answers
    state_dump: dict = field(default_factory=dict)   # what the node had
```

The `Park(reason=f"{question}\nOptions: {options}")` carries enough for the
operator's DM to be actionable. (This is a small step; the bigger
interaction shape — buttons, threaded replies — is out of scope for ticket 32.)

## Acceptance criteria

- [ ] `friday/dag/` exists with `DAG`, `DAGRunner`, `Node`, `Edge`, `DAGState`, `PauseForHuman`
- [ ] `EDGE_ROUTER` is a `dict[str, DAG]` and is the one place to register a workflow for a task type
- [ ] `WorkflowRunner._plan` delegates to `DAGRunner.run`, no longer calls `plan(task_type, params, text)` directly for tasks that have a DAG
- [ ] `PauseForHuman` raised in a node surfaces as a `Park` action with the question in the outbound row
- [ ] After every node, `db.save_dag_state(task.id, state)` runs; on restart, the runner picks up at the next node
- [ ] A DAG with five nodes and one conditional edge runs end-to-end in a test (the test uses a stub for each node — no real MCP, no real LLM)
- [ ] Ticket 28's old "checkpointed procedure" text is renamed and reframed in this ticket; ticket 34 will retire the old text
- [ ] No new dependency on graph libraries (LangGraph, NetworkX) — the runner is the seam, ~150 lines
