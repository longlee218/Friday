"""The graph home: every task type is a graph of nodes and edges, run durably.

One home now, not a `dag`/`workflow` split (ticket 20). `adapter.py` is the
**DBOS adapter** — the one module that imports `dbos` — and durability, step
memoization and resume are its, not a hand-written runner's: the graph
vocabulary itself (`DAG`, `Node`, `Edge`, `Deps`, `DAGState`, `envelope`) lives
in the port at `friday.sdk.workflow`, and callers import it from there directly
now that the `engine.py`/`state.py` compatibility shims are gone.

`prepare.py` builds node 0, which every graph shares. `registry.py` is the one
task-type registry. `router.py` says which graph runs which task type and
nothing else — it names no graph's node names, and no task type. `task_types.py`
registers the one in-core simple type and loads the plugins.

Empty on purpose: importing any submodule runs this first, so whatever lives
here is paid for by every import of the package — which is why the adapter is a
submodule imported where it is used, not from here.
"""
