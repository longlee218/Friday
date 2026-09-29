# 33: api_issue is the first real DAG

**What to build:** The first workflow built on the DAG framework from ticket 32.
api_issue becomes a 5-node DAG that reads logs, locates code, analyses the
stack, optionally fixes the bug, and composes the reply.

**Blocked by:** 32 (the framework this DAG runs on)

**Status:** done

The previous `plan_api_issue` was a 5-line conditional: do we have a
correlation id or a curl? If yes, park; if no, ask. That fits a function.
A workflow that reads logs from Loki, finds the code path, analyses the
stack trace, decides whether the fix is safe, and reports back is not a
function — it is a DAG, and it is the first one the system has.

## Nodes

```
read_logs        →  find_code_path  →  analyze_stack  →  ┬→ fix_bug        →  compose_reply
                                                     └→ compose_reply (skip fix)
```

### 1. `read_logs`

Inputs: `correlation_id`, `environment`, `text_window`.
Calls: `query_loki` MCP tool, returning log lines from the last hour.
Output: a string of log lines, or `None` if no logs found.

The node records the input it used and the result it got into `DAGState`.
A node that reads from Loki is idempotent in the sense that calling it twice
with the same inputs produces the same `None` or the same lines — the runner
relies on this for resume.

### 2. `find_code_path`

Inputs: the log lines, plus a brief summary of what the user reported.
Calls: a `read_file` MCP tool over the source tree, plus a `grep` tool.
Output: the most likely file and line, or a list of candidates if it is
ambiguous.

### 3. `analyze_stack`

Inputs: log lines + file path + line.
Calls: a model call. The node's instructions name the model and the format.
Output: a structured description of the cause — `{"cause": str,
"actionable": bool, "evidence": list[str]}`.

This is the only model call in the api_issue DAG. The other nodes are MCP
or deterministic code. That ratio is the point — the model is the
expensive step, and DAG gives us a place to skip it when not needed.

### 4. `fix_bug`

Inputs: the cause description, plus the file path and line.
Calls: an `edit_file` MCP tool. The patch is applied only if the cause is
"actionable" and the diff is small (< 30 lines, no test changes).

Raises `PauseForHuman` when:
- cause.actionable is False (no obvious fix),
- the diff is too large,
- the change touches a test,
- the cause mentions credentials, migrations, or schema.

Output: the diff text on success, the question string when it pauses.

### 5. `compose_reply`

Inputs: everything in `DAGState` — logs, code path, cause, fix, pause
question (if any).
Calls: a model call. The node's instructions say: write as the watched
account, in their voice, that the user can read in one screen.

Output: the reply text.

## The shape of each node

```python
async def node(state: DAGState, deps: DAGDeps) -> NodeResult:
    """Each node reads from state, calls tools, returns its slice."""
    ...
```

`DAGDeps` is the per-run injection point — DB handle, MCP servers, the
extraction result, the original text. It is the `deps` argument that lets a
node be tested with a stub without touching the database.

## MCP servers, per node

The DAG declares its MCP servers where it needs them. `read_logs` opens a
Loki server inside its own `async with` block; `find_code_path` and `fix_bug`
share a source-tree server; `compose_reply` does not need any.

```python
async with (
    MCPServerStdio(params={"command": "npx", "args": ["-y", "loki-mcp"]}) as loki,
    MCPServerStdio(
        params={"command": "npx", "args": ["-y", "fs-mcp", repo_path]}
    ) as fs,
):
    dag = build_api_issue_dag(loki=loki, fs=fs)
    await dag.run(state)
```

The async-with scope is the DAG runner, not the composition root. The
composition root no longer needs to know about MCP servers for workflows.

## Why this is a DAG and not five calls in one Agent

One Agent with all five tools would mix concerns: the model would have to know
which tool to call when, and the conditional logic ("only fix if actionable")
would be in the prompt. With a DAG, each node has a focused instructions,
the conditional is code, and the runner knows which node it is on for
checkpoint and pause.

The DAG also gives us a place to **stop**. Without it, the model is at the
mercy of max_turns: it may stop after three steps because the prompt
instructs it to, or run all five and run out. The DAG makes the steps
explicit.

## Acceptance criteria

- [x] `friday/dag/api_issue.py` exists and registers `api_issue` with `EDGE_ROUTER`
- [x] The DAG has five nodes and four edges (one of them conditional on `analyze_stack.actionable`)
- [x] Each node is implemented as `async def node(state, deps) -> NodeResult`
- [ ] **Not done.** Loki MCP server is opened inside the DAG runner's `async with`
  block, not at module load. Servers are still opened by the composition root
  and handed to the graph through `DAGDeps.servers`. The graph never opens or
  closes one, which is the half that matters — a node cannot leak a
  subprocess. Moving the `async with` into the runner would mean spawning and
  handshaking a server per task rather than per process, and at one task every
  few minutes that is the wrong trade. **Revisit when a graph needs a server
  the rest of the process should not have**, which is the only thing
  per-run scoping actually buys.
- [x] A test exercises the DAG with stub nodes for each of the five steps and verifies:
  - state accumulates across the run (each node reads + writes)
  - `fix_bug` is skipped when `analyze_stack.actionable is False`
  - `PauseForHuman` from `fix_bug` lands as a Park with the question in `state["fix_bug_pause"]`
  - on resume after a `PauseForHuman`, the DAG starts from the paused node, not from the beginning
- [x] The previous `plan_api_issue` function is removed; the route is the DAG
- [x] Existing triage tests still pass; the route `api_issue → api_issue DAG` is what changes

## Review fixes (after QA)

Four defects the QA pass found, all now fixed with a test each:

- **State outlived its inputs.** The graph asked for a correlationId, the
  reporter supplied it, and the second pass reused the conclusions drawn
  without it — including `read_logs` returning `None` because there was
  nothing to look up. That is a regression against the planner this graph
  replaced, which had no state and so re-planned correctly every time.
  `dag_state` now records a fingerprint of the parameters the graph ran
  against, and state written for different parameters is discarded.
- **`bool("false")` is `True`.** A model that answered `{"actionable":
  "false"}` was read as permission to change code. `_is_yes` now accepts only
  a recognisable yes; everything else resolves towards not touching the code.
- **`fix_bug` was given `find_code_path`'s instructions** — told to locate a
  file, then asked for a diff. It has its own prompt now, with the refusals
  spelled out.
- **Tool servers were only ever an on/off gate.** `read_logs` checked that a
  log server existed and then ran an agent with no tools, which can only
  invent the lines it was asked to look up. Each node's agent is now handed
  the server it needs, and only that one.

## Second review pass

- **Fenced JSON was not read as JSON.** ```` ```json {...} ``` ```` is the
  most ordinary shape a model returns JSON in; unfenced, the whole blob failed
  `startswith("{")`, became the `cause` verbatim, lost a genuine
  `actionable: true` — so the fix edge was never taken — and was then proposed
  as the reply to send under the operator's name.
- **Which server a node needs was stated in two files.** `NODE_SERVERS` lives
  beside the nodes now, and `dag/workflows.py` reads it. Adding a node used to
  mean editing both, with nothing to catch the drift.

## Third review pass — the guard that decides whether code gets edited

- **`{"actionable": true, "cause": null}` disarmed the hands-off guard
  completely.** `_actionable` read only the flag; `_fix_bug` then matched the
  hands-off words against an empty cause, which matches nothing. The fixer was
  handed a migration to patch with no stated reason — and `_compose_reply`,
  seeing a falsy cause, dropped the returned diff on the floor. The change was
  made and never mentioned. A cause is required now.
- **The guard read the model's prose, not the file the fix would touch.** A
  cause of "off-by-one in the loop bound" says nothing about the file it is
  in, and the file was
  `migrations/versions/443468757024_baseline_schema.py`. It matches the
  located path as well now — only the FIX prompt stood between that and a
  patched migration, which is the model policing itself rather than the
  deterministic check the code advertises.
- Both were reachable only because `dag_fix` is commented out in
  `config.yaml`. That is the one agent that edits repositories, so the guard
  was disarmed precisely where it starts to matter.
- `register_api_issue_dag()` was dead, exported, and would raise if anyone
  called it after `register_dags`.
