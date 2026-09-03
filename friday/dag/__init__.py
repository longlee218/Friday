"""Every task type is a graph: nodes, edges, and a runner that checkpoints.

`engine.py` is the framework — `DAG`, `Node`, `Edge`, `DAGDeps`, `DAGRunner`.
`state.py` is what a run accumulates. `prepare.py` builds node 0, which every
graph shares. `api_issue/` is the one graph with an investigation past it, and it owns
everything about itself: each of its nodes declared once, its prompts, and
the agents built from that declaration. `router.py` says which graph runs
which task type and nothing else — it does not know any graph's node names
(ticket 15).

Empty on purpose: importing any submodule runs this first, so whatever lives
here is paid for by every import of the package. The engine lived here until
ticket 13, and the cost was visible — seven imports written inside functions
to dodge the cycle a package that is also a module creates.
"""
