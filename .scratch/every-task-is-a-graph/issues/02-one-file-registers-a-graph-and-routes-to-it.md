# 02: One file registers a graph and routes to it

**What to build:** "Which graph runs this task type, and what is it built
from" is answered by reading one file. Behaviour unchanged, prompts identical.

**Blocked by:** None (can start immediately)

**Decisions:** D4

**Status:** ready-for-agent

## Why

The answer is currently split across two modules that are never read apart.
One holds the type-to-graph dict and the lookup; the other holds the function
that populates it, the per-node agent wiring, and two more module-level dicts
the loop reaches into for a graph's dependencies and tool servers. Three
mutable dicts across two files, all written by one call and read by one caller.

Nothing about that split earns its keep, and the next four tickets all add a
graph or change how one is built.

## Acceptance criteria

- [ ] Registering a graph, looking one up, and building its node agents are
      all in the router module
- [ ] The composition root's call site keeps its shape: one function registers
      every graph and the root learns nothing about any individual one
- [ ] Re-registering in the same process still replaces rather than raising —
      a second startup in a test process is not a wiring mistake
- [ ] A node whose config block or tool server is absent still skips rather
      than fails, and still says so in the log
- [ ] Every assembled prompt is byte-identical before and after, captured
- [ ] The old registration module is gone and nothing imports it
