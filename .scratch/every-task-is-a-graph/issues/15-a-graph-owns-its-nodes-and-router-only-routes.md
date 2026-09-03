# 15: A graph owns its nodes, and the router only routes

**What to build:** Everything that defines one of `api_issue`'s nodes — what it
runs, what it is told, which configuration builds its agent, which tool server
it needs, which tools it gets — is declared once, in one place, by the graph
that owns it. The router goes back to answering one question: which graph runs
which task type.

**Blocked by:** None (can start immediately)

**Decisions:** none new — this is CLAUDE.md's Layout rule applied to a boundary
that drifted

**Status:** ready-for-agent

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

- [ ] Each of `api_issue`'s nodes is declared once, with everything that
      defines it, and both the graph the engine walks and the agent behind the
      node are derived from that single declaration
- [ ] No module outside the graph's own package knows the name of any of its
      nodes — the router included
- [ ] The router's remaining surface is the registry: which graph runs which
      task type, the one-node graph for types with no investigation, and the
      startup call that registers them
- [ ] The graph's prompt module holds its texts and assembles them through the
      shared section builders, not through its own copies of them
- [ ] The engine still receives only a name and a function, and still contains
      no reference to any particular graph
- [ ] `register_dags` keeps its signature — the composition root is untouched
- [ ] No behaviour changes: the suite passes with the same count, and the
      per-node wiring is mutation-tested — which node gets which tools, which
      node is skipped when its configuration block is absent, which node skips
      when its tool server is missing
- [ ] Every assembled prompt is byte-identical before and after
- [ ] CLAUDE.md's layout table describes the package
