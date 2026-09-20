"""Every task type is a graph: nodes, edges, and a runner that checkpoints.

`engine.py` is the framework — `DAG`, `Node`, `Edge`, `DAGDeps`, `DAGRunner`.
`state.py` is what a run accumulates. `prepare.py` builds node 0, which every
graph shares. `router.py` says which graph runs which task type and nothing
else — it does not know any graph's node names.

`api_issue/` is the one graph with an investigation past node 0, and it owns
everything about itself: each of its nodes declared once, its prompts, and
the agent built from that declaration. Every other type is the one-node graph
`router.build_simple_dag` makes.

**That paragraph was false for a while, and the way it went false is worth
keeping.** A five-node `api_issue/` was deleted in `de315e7` and this
docstring was not, so the first thing a reader met when they opened this
package described a directory that held nothing but a stale `__pycache__`.
The package exists again — board `read-it-the-way-the-operator-does`, ticket
00 — and it is a different graph: the deleted one was a guess at what
investigating an API fault looks like, this one is a transcription of the
operator's routine, and its nodes skip out loud instead of silently.

Empty on purpose: importing any submodule runs this first, so whatever lives
here is paid for by every import of the package. The engine lived here until
ticket 13, and the cost was visible — seven imports written inside functions
to dodge the cycle a package that is also a module creates.
"""
