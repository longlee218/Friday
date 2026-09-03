# 15: A graph owns its nodes, and the router only routes

**What to build:** Everything that defines one of `api_issue`'s nodes — what it
runs, what it is told, which configuration builds its agent, which tool server
it needs, which tools it gets — is declared once, in one place, by the graph
that owns it. The router goes back to answering one question: which graph runs
which task type.

**Blocked by:** None (can start immediately)

**Decisions:** none new — this is CLAUDE.md's Layout rule applied to a boundary
that drifted

**Status:** done

## Why

Raised by the operator reading the code: *"phần DAG đang nhập nhằng giữa
api_issue và các thành phần engine core."* Measured, the core is clean — the
engine, the state and node 0 contain no reference to `api_issue` at all. The
drift is on the other side of the package.

**Six things are keyed by the same five node names, across three files.** Node
to server; node to configuration block; node to prompt text; which nodes reason
and so get the skills catalogue; a chain deciding which node gets which tools;
and a branch deciding which persona family a node is built with. One of those
six sits beside a comment that argues for exactly the rule the other five
break: *"Stated in two files, those two would drift and nothing would catch
it."*

Six dicts and branch-chains over one keyspace is a type asking to be born, and
the router carries a third of them while its own stated job is only to map a
task type to a graph.

**One declaration, two projections.** The first draft of this ticket proposed
a second table beside the existing nodes — which the operator correctly read as
the same disease at half strength: two dicts, one keyspace. So the graph
declares each node once, and derives from that both the node the engine walks
and the agent that sits behind it. The engine learns nothing new: it still
receives a name and a function, and still knows nothing about agents, models or
configuration.

**`api_issue` becomes a package**, so the graph's shape, its prompts and its
wiring are three files with one subject rather than three subjects in one file.
Its `__init__.py` stays empty, for the reason ticket 13 established.

**Its prompt module may use the shared section builders** — the operator's
rule, and it costs nothing: `friday/agent/instruction_prompt.py` already owns
the shape of a section and the one escaping seam, a graph's prompt module
already reaches for it, and going through it is what keeps every agent's prompt
the same shape.

**Not in this ticket:** the persona family. The declaration gains a family
field like any other property of an agent, and nothing about `Family` or
`PERSONA.md` changes here — ticket 16 takes that up, and it needs a test
re-anchored before anything can be deleted.

**Also not in this ticket:** lifting the prompt assembly into shared mechanism.
There is one graph with agents. When a second appears, that is the moment to
design the shared form against two callers rather than guess at it with one.
Saying so here so the next reader knows it was declined rather than missed.

## Acceptance criteria

- [x] Each of `api_issue`'s nodes is declared once, with everything that
      defines it, and both the graph the engine walks and the agent behind the
      node are derived from that single declaration
- [x] No module outside the graph's own package knows the name of any of its
      nodes — the router included
- [x] The router's remaining surface is the registry: which graph runs which
      task type, the one-node graph for types with no investigation, and the
      startup call that registers them
- [x] The graph's prompt module holds its texts and assembles them through the
      shared section builders, not through its own copies of them
- [x] The engine still receives only a name and a function, and still contains
      no reference to any particular graph
- [x] `register_dags` keeps its signature — the composition root is untouched
- [x] No behaviour changes: the suite passes with the same count, and the
      per-node wiring is mutation-tested — which node gets which tools, which
      node is skipped when its configuration block is absent, which node skips
      when its tool server is missing
- [x] Every assembled prompt is byte-identical before and after
- [x] CLAUDE.md's layout table describes the package

## What it came to

`friday/dag/api_issue/` is a package — `graph.py` and `prompt.py` moved with
`git mv` so the history follows, `__init__.py` empty for ticket 13's reason.

**The six tables are one.** `NODES` maps each name to a frozen `_Node` holding
what it runs, its configuration block, its prompt, its family, its server, its
tools, its capture type, whether it reasons, and whether its answer arrives as
a tool call. Two functions read it and nothing else does: `build_api_issue_dag`
projects `Node(name, spec.run)` for the engine, `build_agents` projects a
`Harness`. The engine's side of that projection is unchanged — it still gets a
name and a function.

`NODE_SERVERS` is gone, and with it the comment that argued for the rule the
other five tables broke. A node now asks `_has_server(deps, name)`, which reads
the same row the builder reads, so the two readers the comment worried about
are reading one declaration.

**`prompt.py` stopped knowing which node is which.** `_TEXTS` and `REASONING`
were two of the six, keyed by node name, in a module whose subject is wording.
`build_instructions` takes the text, the family and whether the node reasons as
arguments; the `if node == "compose_reply"` that picked a persona family is
gone, because the family is a field on the node's own declaration now.

**`router.py`: 236 lines to 150**, and it names no node of any graph — twelve
mentions of `api_issue` remain and all twelve are the *task type*, which is
exactly what a router is for. It asks the graph to build its own agents and
logs what came back. Five imports it only needed for agent-building went with
the code.

Two tests got stronger rather than merely relocated, because one table can be
asked questions three files could not:

- The D17 approval test read "`fix_bug` has a configuration block" and took
  the rest on trust. It now walks every node's declared tools and asserts that
  the set carrying `needs_approval` is exactly `{"fix_bug"}`.
- The Responder invariant's first half grepped for `Family.RESPONDER` in
  allowed files; it now also asserts directly that `compose_reply` is the only
  node *claiming* that family, then builds from each declaration to check the
  claim is honoured.

Verified: 642 tests, the same 642 as before. Prompts byte-identical. The four
per-node guards mutation-tested — remove `compose_reply`'s tools, `fix_bug`'s
tools, the skip for a node with no configuration block, or the skip for a node
with no server, and a named test goes red for each.

## What the review caught

A standards review found a guard this ticket had killed, and it was the kind
this repo has been bitten by before: **`test_no_family_imports_another_familys_prompt_module` derived the module
name from the family** — `friday.<family>.prompt` — and grepped for it. Once
the graph's module moved to `friday.dag.api_issue.prompt`, that grep looked
for a module that does not exist, so it passed no matter what any family did.
Nothing failed. Proved by adding the import to `friday/responder/prompt.py`
and watching the test stay green.

Fixing it turned up a **second hole that predates this ticket**: grep found
`from x.y.prompt import Z` and missed `from x.y import prompt`, because the
dotted module name never appears in the second form. The check reads the
imports with `ast` now and all three spellings are caught — proved by
mutating each one in turn.

Two documentation claims went stale in a commit that edited documentation.
`friday/dag/__init__.py` still said the router "builds their agents", which
is the thing this ticket removed, and CLAUDE.md's prompt-family rule still
said all four modules are `friday.<family>.prompt`. Both corrected, and
CLAUDE.md now records *why* the exception exists rather than just stating it.

Two judgement calls taken: the two-part readiness check each node repeated —
agent present, and its server there — is one `_agent_for(deps, name)`, which
also drops one restatement of the node's own name per node; and `_Node.run`
carries the engine's own `NodeFn` rather than `Any`, since that field is what
gets handed to the engine.

Re-verified after all of it: 642 tests, prompts byte-identical, and the
merged readiness check mutation-tested red.
