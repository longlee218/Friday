# 44: A node's prompt is the graph's business

**What to build:** One module owning how a graph node's prompt is assembled:
the Node persona, the node's own text, the skills catalogue for reasoning
nodes, and the channel sections for the one node that writes to a person.
Today that assembly lives inside the wiring function that also builds agents,
picks servers and reads config — four jobs in one place.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

## Acceptance criteria

- [ ] `build_instructions(node, persona, skills)` in the graph's own prompt
      module; the wiring function wires and assembles nothing
- [ ] The per-node user turns (the evidence handed to analyze, the said/fix
      handed to compose) stay in the node functions — they are the node's
      logic, not prompt furniture; the boundary is written down in the module
- [ ] Output byte-identical to today's, captured not eyeballed
- [ ] The existing family tests (voice in compose, no voice in analyze) pass
      untouched
