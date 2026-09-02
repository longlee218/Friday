# 02: One file registers a graph and routes to it

**What to build:** "Which graph runs this task type, and what is it built
from" is answered by reading one file. Behaviour unchanged, prompts identical.

**Blocked by:** None (can start immediately)

**Decisions:** D4

**Status:** done

## Why

The answer is currently split across two modules that are never read apart.
One holds the type-to-graph dict and the lookup; the other holds the function
that populates it, the per-node agent wiring, and two more module-level dicts
the loop reaches into for a graph's dependencies and tool servers. Three
mutable dicts across two files, all written by one call and read by one caller.

Nothing about that split earns its keep, and the next four tickets all add a
graph or change how one is built.

## Acceptance criteria

- [x] Registering a graph, looking one up, and building its node agents are
      all in the router module
- [x] The composition root's call site keeps its shape: one function registers
      every graph and the root learns nothing about any individual one
- [x] Re-registering in the same process still replaces rather than raising —
      a second startup in a test process is not a wiring mistake
- [x] A node whose config block or tool server is absent still skips rather
      than fails, and still says so in the log
- [x] Every assembled prompt is byte-identical before and after, captured
- [x] The old registration module is gone and nothing imports it

## What it came to

`friday/dag/workflows.py` is gone; its contents — `_API_ISSUE_AGENTS`,
`agents_for_api_issue`, `register_dags`, `DAG_DEPS_EXTRA`, `DAG_SERVERS` —
moved into `friday/dag/router.py`, unchanged, below the `EDGE_ROUTER` /
`register_dag` / `dag_for` that were already there. No cycle to break: neither
half imported the other, they were just read as one unit by every caller and
filed as two.

Six call sites updated (`run_agent.py`, `friday/workflows/runner.py`, a
comment in `friday/dag/api_issue.py`, and four test files); one redundant
import line in `tests/conftest.py` folded into the other. `friday.dag.router`
now exports `register_dags` and `agents_for_api_issue` too, alongside the two
names it already had.

`friday/*/prompt.py` untouched, so byte identity holds by construction. 610
passed, same count as before — this ticket added no new test, only moved code
an existing test already covered.
